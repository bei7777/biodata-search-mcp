import asyncio
import re
from urllib.parse import quote

import httpx

from .international import cursor_window

DATABASES = {"articles": "Europe PMC — research literature"}
_SOURCE = r"(?:MED|PMC|PPR|AGR|PAT|CBA|HIR|CTX|ETH|CIT|NBK)"
AUTO_ID_PATTERN = rf"{_SOURCE}:[A-Z0-9][A-Z0-9._-]*"
_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _record(row: dict, details: bool = False) -> dict:
    source, accession = row.get("source"), row.get("id")
    if not isinstance(source, str) or not isinstance(accession, str) or not source or not accession:
        raise RuntimeError("Europe PMC 记录缺少来源或编号")
    record = {
        "id": f"{source}:{accession}", "title": row.get("title") or accession,
        "url": f"https://europepmc.org/article/{quote(source, safe='')}/{quote(accession, safe='')}",
    }
    if details:
        record["metadata"] = {
            key: row[key] for key in (
                "source", "pmid", "pmcid", "doi", "authorString", "pubYear", "journalInfo",
                "firstPublicationDate", "isOpenAccess", "citedByCount", "pubTypeList",
            ) if row.get(key) is not None
        }
    return record


async def _request(client: httpx.AsyncClient, **params) -> tuple[int, list[dict], str | None]:
    query = {"format": "json", **params}
    response = await client.get(_SEARCH, params=query)
    if response.status_code in {502, 503, 504}:
        delay = response.headers.get("retry-after", "1")
        if delay.isdecimal() and int(delay) <= 5:
            await asyncio.sleep(int(delay))
            response = await client.get(_SEARCH, params=query)
    response.raise_for_status()
    data = response.json()
    results = data.get("resultList") if isinstance(data, dict) else None
    rows = results.get("result") if isinstance(results, dict) else None
    count = data.get("hitCount") if isinstance(data, dict) else None
    if (not isinstance(count, int) or count < 0 or not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows)):
        raise RuntimeError("Europe PMC 未返回有效的记录页")
    return count, rows, data.get("nextCursorMark")


async def search(client: httpx.AsyncClient, database: str, query: str, limit: int, offset: int) -> dict:
    async def fetch(size, cursor):
        return await _request(client, query=query, pageSize=size, cursorMark=cursor or "*", resultType="lite")

    total, rows = await cursor_window(fetch, limit, offset, 1000)
    return {"total": total, "records": [_record(row) for row in rows]}


async def lookup(client: httpx.AsyncClient, database: str, identifier: str) -> dict | None:
    value = identifier.strip().upper()
    pubmed = re.fullmatch(r"(?:PMID:\s*)?(\d+)", value)
    pmcid = re.fullmatch(r"(?:PMCID:\s*)?(PMC\d+)", value)
    if pubmed:
        value = "MED:" + pubmed[1]
    if pmcid:
        query = f"PMCID:{pmcid[1]}"
    else:
        if not re.fullmatch(AUTO_ID_PATTERN, value):
            raise ValueError("Europe PMC 接受来源与编号（如 MED:29535343）、PMID 或 PMCID")
        source, accession = value.split(":", 1)
        query = f"EXT_ID:{accession} AND SRC:{source}"
    _, rows, _ = await _request(client, query=query, pageSize=10, resultType="core")
    for row in rows:
        record = _record(row, details=True)
        if (pmcid and row.get("pmcid") == pmcid[1]) or record["id"].upper() == value:
            return record
    return None
