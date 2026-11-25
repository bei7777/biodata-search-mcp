import json
import re
from importlib.resources import files
from urllib.parse import quote, urljoin, urlsplit

import httpx

HOST = "https://ngdc.cncb.ac.cn"
DATABASES: dict[str, str] = json.loads(
    files(__package__).joinpath("ngdc_indexes.json").read_text(encoding="utf-8")
)


async def _index_page(client: httpx.AsyncClient, database: str, query: str, limit: int, offset: int) -> dict:
    if database not in DATABASES:
        raise ValueError(f"未知的 NGDC 索引 {database}")
    response = await client.get(
        f"{HOST}/search/api/specific",
        params={"db": database, "q": query, "length": limit, "start": offset},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("code")) != "200":
        raise RuntimeError(f"NGDC 未能完成 {database} 检索")
    result = payload.get("result")
    page = result.get("data") if isinstance(result, dict) else None
    if (
        not isinstance(page, dict)
        or page.get("error")
        or not isinstance(page.get("data"), list)
        or not isinstance(page.get("recordsTotal"), int)
        or any(not isinstance(row, dict) or not isinstance(row.get("attrs") or {}, dict) for row in page["data"])
        or not all(isinstance(row, dict) for row in page["data"])
    ):
        raise RuntimeError(f"NGDC 未返回 {database} 的有效结果页")
    return page


def _index_record(row: dict, database: str, *, details: bool) -> dict | None:
    attrs = row.get("attrs") or {}
    if database == "hra" and attrs.get("Release state") == "Not released":
        return None
    url = row.get("url")
    if not isinstance(url, str) or not row.get("id"):
        return None
    if url.startswith("/"):
        url = urljoin(HOST, url)
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        return None
    identifier = str(row["id"])
    record = {
        "id": identifier,
        "title": str(row.get("title") or row.get("type") or identifier),
        "url": url,
    }
    if row.get("description"):
        record["description"] = str(row["description"])[:4000 if details else 350]
    if details:
        record["detail_source"] = "NGDC BIG Search"
        record["metadata"] = {
            "type": row.get("type"),
            "species": row.get("species"),
            "updated_at": row.get("updatedAt"),
            "attributes": attrs,
        }
    return record


async def search(client: httpx.AsyncClient, database: str, query: str, limit: int, offset: int) -> dict:
    page = await _index_page(client, database, query, limit, offset)
    rows = page["data"]
    records = []
    for row in rows:
        record = _index_record(row, database, details=False)
        if record is not None:
            records.append(record)
    return {
        "total": page["recordsTotal"],
        "records": records,
        "scanned": len(rows),
        "omitted_without_url": len(rows) - len(records),
    }


_GWH_FIELDS = {
    "assembly": ("assemblyAccession", "assemblyName", "/gwh/Assembly/{id}/show"),
    "genome": ("id", "organism", "/gwh/Genome/{id}/show"),
    "bioProject": ("prjAccession", "title", "/bioproject/browse/{accession}"),
    "bioSample": ("accession", "title", "/biosample/browse/{accession}"),
}


async def _gwh_detail(client: httpx.AsyncClient, route: str, identifier: str) -> dict | None:
    api_url = f"{HOST}/gwh/api/public/{route}/{quote(identifier, safe='')}"
    response = await client.get(api_url)
    response.raise_for_status()
    data = response.json()
    id_field, title_field, web_path = _GWH_FIELDS[route]
    if not isinstance(data, dict) or id_field not in data:
        raise RuntimeError(f"GWH 未返回预期的 {route} 详情字段")
    if str(data[id_field]).casefold() != identifier.casefold():
        return None
    if route in {"assembly", "genome"} and not str(data.get("id")).isdigit():
        raise RuntimeError("GWH 未返回原始记录的网页编号")
    metadata = {
        key: value for key, value in data.items()
        if value is not None and key not in {"contacts", "submitter", "userId"}
    }
    return {
        "id": str(data[id_field]),
        "title": str(data.get(title_field) or identifier),
        "url": HOST + web_path.format(id=data.get("id"), accession=identifier),
        "detail_source": "GWH API",
        "api_url": api_url,
        "metadata": metadata,
    }


async def _rice_or_icg_detail(client: httpx.AsyncClient, database: str, identifier: str) -> dict | None:
    if database == "ic4r":
        api_path = f"/ic4r/api/json/gene/{identifier}"
        web_path = f"/ic4r/genes/{identifier}"
        key, title = "locus", "description"
    else:
        api_path = f"/icg/api/json/species/{identifier}"
        web_path = f"/icg/species/accession/{identifier}"
        key, title = "accession", "latinName"
    response = await client.get(HOST + api_path)
    response.raise_for_status()
    if not response.content.strip():
        return None
    data = response.json()
    if database == "icg":
        if not isinstance(data, dict) or str(data.get("code")) not in {"200", "300"}:
            raise RuntimeError("ICG 未返回有效的查询状态")
        data = data.get("result")
    if data is None:
        return None
    if not isinstance(data, dict) or key not in data:
        raise RuntimeError(f"{database} 未返回预期的编号字段")
    if str(data[key]).casefold() != identifier.casefold():
        return None
    return {
        "id": str(data[key]),
        "title": str(data.get(title) or identifier),
        "url": HOST + web_path,
        "detail_source": f"{database.upper()} API",
        "api_url": HOST + api_path,
        "metadata": {k: v for k, v in data.items() if v is not None and k != "projectList"},
    }


async def lookup(client: httpx.AsyncClient, database: str, identifier: str) -> dict | None:
    accession = identifier.upper()
    route = None
    if database == "gwh":
        if identifier.isascii() and identifier.isdecimal():
            return await _gwh_detail(client, "genome", identifier)
        if re.fullmatch(r"GWH[A-Z]+[0-9]+(?:\.[0-9]+)?", accession):
            route = "assembly"
    elif database == "bioproject" and re.fullmatch(r"PRJCA[0-9]+", accession):
        route = "bioProject"
    elif database == "biosample" and re.fullmatch(r"SAMC[0-9]+", accession):
        route = "bioSample"
    if route:
        direct = await _gwh_detail(client, route, accession)
        if direct is not None:
            return direct
    if (database == "icg" and re.fullmatch(r"ICG[0-9]+", accession)) or (
        database == "ic4r" and re.fullmatch(r"IC4R-OSJ[0-9]+G[0-9]+", accession)
    ):
        direct = await _rice_or_icg_detail(client, database, accession)
        if direct is not None:
            return direct
    # BIG Search 支持按 id 字段检索。再校验原始编号，避免将关联项目当作所查询的记录。
    query = "id:" + json.dumps(identifier, ensure_ascii=False)
    page = await _index_page(client, database, query, 25, 0)
    for row in page["data"]:
        if str(row.get("id", "")).casefold() == identifier.casefold():
            record = _index_record(row, database, details=True)
            if record is not None:
                return record
    return None
