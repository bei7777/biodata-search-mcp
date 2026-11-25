import asyncio
import json
import re

import httpx

DATABASES = {"entry": "RCSB Protein Data Bank — experimental structures"}
_LEGACY = r"[1-9][A-Z0-9]{3}"
_EXTENDED = r"PDB_[A-Z0-9]{8}"
AUTO_ID_PATTERN = rf"(?:(?!\d{{4}}$){_LEGACY}|{_EXTENDED})"
_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
_ENTRIES = "https://data.rcsb.org/rest/v1/core/entry"


async def _entry(client: httpx.AsyncClient, identifier: str, details: bool) -> dict | None:
    response = await client.get(f"{_ENTRIES}/{identifier}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    pdb_id = data.get("rcsb_id") if isinstance(data, dict) else None
    if not isinstance(pdb_id, str) or not re.fullmatch(rf"{_LEGACY}|{_EXTENDED}", pdb_id.upper()):
        raise RuntimeError("RCSB PDB 未返回记录编号")
    pdb_id = pdb_id.upper()
    if pdb_id != identifier and "PDB_0000" + pdb_id != identifier:
        raise RuntimeError("RCSB PDB 返回的编号与指定结构不一致")
    record = {
        "id": pdb_id, "title": data.get("struct", {}).get("title") or pdb_id,
        "url": f"https://www.rcsb.org/structure/{pdb_id}",
    }
    if details:
        info = data.get("rcsb_entry_info", {})
        metadata = {
            "experimental_methods": [exp["method"] for exp in data.get("exptl", []) if exp.get("method")],
            "resolution_angstroms": info.get("resolution_combined"),
            "release_date": data.get("rcsb_accession_info", {}).get("initial_release_date"),
            "citation_doi": data.get("rcsb_primary_citation", {}).get("pdbx_database_id_DOI"),
        }
        record["metadata"] = {key: value for key, value in metadata.items() if value}
    return record


async def search(client: httpx.AsyncClient, database: str, query: str, limit: int, offset: int) -> dict:
    request = {
        "query": {"type": "terminal", "service": "full_text", "parameters": {"value": query}},
        "return_type": "entry",
        "request_options": {"paginate": {"start": offset, "rows": limit}},
    }
    response = await client.get(_SEARCH, params={"json": json.dumps(request, separators=(",", ":"))})
    if response.status_code == 204:
        return {"total": 0, "records": []}
    response.raise_for_status()
    page = response.json()
    total = page.get("total_count") if isinstance(page, dict) else None
    rows = page.get("result_set", []) if isinstance(page, dict) else None
    if (not isinstance(total, int) or total < 0 or not isinstance(rows, list)
            or any(not isinstance(row, dict) for row in rows) or (offset < total and not rows)):
        raise RuntimeError("RCSB PDB 未返回有效的检索结果页")
    ids = [row.get("identifier") for row in rows]
    if not all(isinstance(pdb_id, str) and re.fullmatch(rf"{_LEGACY}|{_EXTENDED}", pdb_id.upper()) for pdb_id in ids):
        raise RuntimeError("RCSB PDB 检索结果缺少有效结构编号")
    records = await asyncio.gather(*(_entry(client, pdb_id.upper(), False) for pdb_id in ids))
    if any(record is None for record in records):
        raise RuntimeError("RCSB PDB 搜索索引中的条目已不可读取")
    return {"total": total, "records": records}


async def lookup(client: httpx.AsyncClient, database: str, identifier: str) -> dict | None:
    pdb_id = identifier.strip().upper()
    if not re.fullmatch(rf"{_LEGACY}|{_EXTENDED}", pdb_id):
        raise ValueError("请使用 PDB 结构编号，例如 4HHB 或 PDB_00004HHB")
    return await _entry(client, pdb_id, True)
