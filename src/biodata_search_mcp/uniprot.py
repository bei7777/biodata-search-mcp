import csv
import io
import re

import httpx

from .international import cursor_window

DATABASES = {"uniprotkb": "UniProt Consortium — UniProtKB"}
AUTO_ID_PATTERN = r"(?:[OPQ]\d[A-Z0-9]{3}\d|[A-NR-Z]\d(?:[A-Z][A-Z0-9]{2}\d){1,2})"
_BASE = "https://rest.uniprot.org/uniprotkb"
_FIELDS = "accession,id,protein_name,gene_names,organism_name,organism_id,length,reviewed"


def _rows(response: httpx.Response) -> list[dict]:
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text), delimiter="\t")
    if not {"Entry", "Protein names"}.issubset(reader.fieldnames or []):
        raise RuntimeError("UniProtKB 未返回预期的表格字段")
    return list(reader)


def _record(row: dict, details: bool = False) -> dict:
    accession = row["Entry"]
    if not isinstance(accession, str) or not re.fullmatch(AUTO_ID_PATTERN, accession):
        raise RuntimeError("UniProtKB 返回了无效的蛋白质编号")
    record = {
        "id": accession, "title": row.get("Protein names") or row.get("Entry Name") or accession,
        "url": f"https://www.uniprot.org/uniprotkb/{accession}/entry",
    }
    if details:
        record["metadata"] = {key: value for key, value in row.items() if value and key != "Protein names"}
    return record


async def search(client: httpx.AsyncClient, database: str, query: str, limit: int, offset: int) -> dict:
    async def fetch(size, cursor):
        params = {"query": query, "format": "tsv", "fields": _FIELDS, "size": size}
        if cursor:
            params["cursor"] = cursor
        response = await client.get(f"{_BASE}/search", params=params)
        rows = _rows(response)
        total = response.headers.get("x-total-results", "")
        if not total.isdigit():
            raise RuntimeError("UniProtKB 未返回命中总数")
        link = response.links.get("next")
        next_cursor = httpx.URL(link["url"]).params.get("cursor") if link else None
        return int(total), rows, next_cursor

    total, rows = await cursor_window(fetch, limit, offset, 500)
    return {"total": total, "records": [_record(row) for row in rows]}


async def lookup(client: httpx.AsyncClient, database: str, identifier: str) -> dict | None:
    accession = identifier.strip().upper()
    if not re.fullmatch(AUTO_ID_PATTERN, accession):
        raise ValueError("请使用 UniProtKB 主登录号，例如 P05067 或 A0A024RBG1")
    response = await client.get(f"{_BASE}/{accession}.tsv", params={"fields": _FIELDS})
    if response.status_code == 404:
        return None
    for row in _rows(response):
        if row["Entry"] == accession:
            return _record(row, details=True)
    return None
