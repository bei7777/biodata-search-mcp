import asyncio
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints

from . import europepmc, ncbi, ngdc, rcsb, uniprot

SOURCES = {"ngdc": ngdc, "ncbi": ncbi, "europepmc": europepmc, "uniprot": uniprot, "rcsb": rcsb}
PROVIDER_NAMES = {
    "ngdc": "National Genomics Data Center", "ncbi": "National Center for Biotechnology Information",
    "europepmc": "Europe PMC", "uniprot": "UniProt Consortium", "rcsb": "RCSB Protein Data Bank",
}
PROVIDER_DEFAULTS = {
    "ngdc": ("ngdc:gsa", "ngdc:omix"), "ncbi": ("ncbi:bioproject", "ncbi:gds", "ncbi:pubmed"),
    "europepmc": ("europepmc:articles",), "uniprot": ("uniprot:uniprotkb",), "rcsb": ("rcsb:entry",),
}
DATABASE_NAMES = {
    f"{provider}:{code}": title
    for provider, source in SOURCES.items()
    for code, title in source.DATABASES.items()
}
CANONICAL = {name.casefold(): name for name in DATABASE_NAMES}
CANONICAL["ncbi:nucleotide"] = "ncbi:nuccore"

# 两个中心都使用纯数字编号，库名由调用者指定。
ID_PATTERNS = {
    "ngdc:gwh": r"GWH[A-Z]{4}\d+(?:\.\d+)?",
    "ngdc:bioproject": r"PRJCA\d+",
    "ngdc:biosample": r"SAMC\d+",
    "ngdc:gsa": r"CR[ARX]\d+",
    "ngdc:omix": r"OMIX\d+",
    "ngdc:nebulas": r"GEND\d+",
    "ngdc:icg": r"ICG\d+(?:-[\w.-]+)?",
    "ngdc:ic4r": r"IC4R-[\w.-]+",
    "ncbi:bioproject": r"PRJ(?:NA|EB|DB)\d+",
    "ncbi:biosample": r"SAM(?:N|EA|D)\d+",
    "ncbi:sra": r"[SED]R[PRSX]\d+",
    "ncbi:gds": r"G(?:SE|DS)\d+",
    "ncbi:assembly": r"GC[AF]_\d+(?:\.\d+)?",
    "ncbi:pmc": r"PMC\d+",
    "ncbi:snp": r"rs\d+",
    "ncbi:nuccore": r"(?:N[CGMRTWZ]|X[MR]|AC|AP)_[A-Z]*\d+(?:\.\d+)?",
    "ncbi:protein": r"(?:NP|XP|YP|WP)_[0-9]+(?:\.\d+)?",
    "europepmc:articles": europepmc.AUTO_ID_PATTERN,
    "uniprot:uniprotkb": uniprot.AUTO_ID_PATTERN,
    "rcsb:entry": rcsb.AUTO_ID_PATTERN,
}
NUMBER_PREFIXES = {"pmid": "ncbi:pubmed", "geneid": "ncbi:gene", "taxid": "ncbi:taxonomy", "cid": "ncbi:pccompound"}
Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[tuple[httpx.AsyncClient, asyncio.Semaphore]]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(25, connect=10), follow_redirects=True,
        headers={"User-Agent": "biodata-search-mcp/0.2.0"},
    ) as client:
        yield client, asyncio.Semaphore(6)


mcp = FastMCP(
    "BioData Search",
    instructions="使用 search_records 检索关键词，使用 get_record 按编号读取详情。引用结果时保留每条记录的 url。",
    lifespan=lifespan,
    log_level="WARNING",
)


def resolve_database(name: str) -> str:
    database = CANONICAL.get(name.strip().casefold())
    if database is None:
        raise ValueError(f"未知数据库 {name!r}。请用 list_databases 查看完整名称，例如 ngdc:omix。")
    return database


def selected_providers(names: list[str]) -> list[str]:
    providers = list(dict.fromkeys(name.strip().casefold() for name in names))
    unknown = [name for name in providers if name not in SOURCES]
    if unknown:
        raise ValueError(f"未知来源 {unknown!r}。可选值为 {', '.join(SOURCES)}。")
    return providers


def default_providers() -> list[str]:
    return selected_providers(os.environ.get("BIODATA_SEARCH_PROVIDERS", "ngdc,ncbi").split(","))


def provider_databases(providers: list[str]) -> list[str]:
    return [database for provider in providers for database in PROVIDER_DEFAULTS[provider]]


def resolve_identifier(identifier: str, database: str | None) -> tuple[str, str]:
    explicit = resolve_database(database) if database is not None else None
    if explicit == "europepmc:articles":
        return explicit, identifier
    prefixed = re.fullmatch(r"(PMID|GeneID|TaxID|CID)\s*:\s*(\d+)", identifier, re.IGNORECASE)
    if prefixed:
        target = NUMBER_PREFIXES[prefixed[1].casefold()]
        if explicit and explicit != target:
            raise ValueError(f"编号前缀对应 {target}，与指定数据库 {explicit} 不符。")
        return target, prefixed[2]
    if explicit:
        return explicit, identifier
    for target, pattern in ID_PATTERNS.items():
        if re.fullmatch(pattern, identifier, re.IGNORECASE):
            return target, identifier
    raise ValueError("此编号需要指定 database。纯数字可配合 ncbi:pubmed、ncbi:gene、ncbi:taxonomy 等数据库使用。")


def upstream_message(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"上游返回 HTTP {error.response.status_code}"
    if isinstance(error, httpx.RequestError):
        return f"网络请求失败（{type(error).__name__}）"
    return str(error)


ProviderList = Annotated[list[str], Field(min_length=1)]


@mcp.tool(annotations=READ_ONLY)
def list_databases(providers: ProviderList | None = None) -> dict[str, Any]:
    """列出可用来源、数据库名称和默认检索范围。providers 可传 ngdc、ncbi、europepmc、uniprot、rcsb，例如 [\"uniprot\",\"rcsb\"]。"""
    visible = selected_providers(providers) if providers is not None else list(SOURCES)
    defaults = visible if providers is not None else default_providers()
    return {
        "providers": [
            {"id": key, "name": PROVIDER_NAMES[key], "default_databases": list(PROVIDER_DEFAULTS[key])}
            for key in visible
        ],
        "default_databases": provider_databases(defaults),
        "databases": [
            {"provider": key, "database": f"{key}:{code}", "name": title}
            for key in visible for code, title in SOURCES[key].DATABASES.items()
        ],
    }


@mcp.tool(annotations=READ_ONLY)
async def search_records(
    ctx: Context, query: Query,
    databases: Annotated[list[str], Field(min_length=1)] | None = None,
    limit: Annotated[int, Field(ge=1, le=10)] = 3,
    offset: Annotated[int, Field(ge=0)] = 0,
    providers: ProviderList | None = None,
) -> dict[str, Any]:
    """按关键词跨库检索。providers 选择来源（ngdc、ncbi、europepmc、uniprot、rcsb）；databases 可指定具体数据库。仅传来源时检索各来源的默认库，同时指定时数据库须属于所选来源。limit、offset 对每个库分别生效，结果保留原始记录链接。"""
    requested = selected_providers(providers) if providers is not None else None
    if databases is None:
        selected = provider_databases(requested if requested is not None else default_providers())
    else:
        selected = list(dict.fromkeys(resolve_database(name) for name in databases))
        if requested is not None and any(db.split(":", 1)[0] not in requested for db in selected):
            raise ValueError("指定的数据库不属于所选来源。请通过 list_databases 查看来源及数据库名称。")
    client, slots = ctx.request_context.lifespan_context

    async def query_database(database: str) -> dict:
        provider, code = database.split(":", 1)
        async with slots:
            try:
                result = await SOURCES[provider].search(client, code, query, limit, offset)
            except (httpx.HTTPError, ValueError, RuntimeError) as error:
                return {"database": database, "error": upstream_message(error)}
        consumed = result.get("scanned", len(result["records"]))
        next_offset = offset + consumed if consumed and offset + consumed < result["total"] else None
        return {"database": database, **result, "next_offset": next_offset}

    results = await asyncio.gather(*(query_database(database) for database in selected))
    failures = [group for group in results if "error" in group]
    if len(failures) == len(results):
        raise RuntimeError("；".join(f"{group['database']} {group['error']}" for group in failures))
    return {"query": query, "limit": limit, "offset": offset, "partial": bool(failures), "results": results}


@mcp.tool(annotations=READ_ONLY)
async def get_record(ctx: Context, identifier: Identifier, database: str | None = None) -> dict[str, Any]:
    """按编号读取详情和原始链接。可自动识别 OMIX、GWH、GSE、PMC、RefSeq、Europe PMC 来源编号（MED:29535343）、UniProt 登录号（P05067）和 PDB 编号（4HHB）等。纯数字请指定 database 或编号类型。"""
    database, accession = resolve_identifier(identifier, database)
    provider, code = database.split(":", 1)
    client, slots = ctx.request_context.lifespan_context
    async with slots:
        try:
            record = await SOURCES[provider].lookup(client, code, accession)
        except httpx.HTTPError as error:
            raise RuntimeError(upstream_message(error)) from None
    return {"database": database, "identifier": identifier, "record": record}


def main() -> None:
    mcp.run(transport="stdio")
