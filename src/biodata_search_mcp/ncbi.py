import asyncio
import re
import time
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

import httpx

DATABASES = {
    "annotinfo": "Annotation Information", "assembly": "Assembly",
    "biocollections": "BioCollections", "bioproject": "BioProject",
    "biosample": "BioSample", "blastdbinfo": "BLAST Database Information",
    "books": "Bookshelf", "cdd": "Conserved Domains", "clinvar": "ClinVar",
    "dbvar": "dbVar", "gap": "dbGaP", "gds": "GEO DataSets",
    "gene": "Gene", "genome": "Genome", "geoprofiles": "GEO Profiles",
    "grasp": "GRASP", "gtr": "Genetic Testing Registry",
    "ipg": "Identical Protein Groups", "medgen": "MedGen", "mesh": "MeSH",
    "nlmcatalog": "NLM Catalog", "nuccore": "Nucleotide", "omim": "OMIM",
    "orgtrack": "Organization Tracking", "pcassay": "PubChem BioAssay",
    "pccompound": "PubChem Compound", "pcsubstance": "PubChem Substance",
    "pmc": "PubMed Central", "protein": "Protein",
    "proteinclusters": "Protein Clusters", "protfam": "Protein Family Models",
    "pubmed": "PubMed", "seqannot": "Sequence Annotation", "snp": "dbSNP",
    "sra": "Sequence Read Archive", "structure": "Structure", "taxonomy": "Taxonomy",
}

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_NCBI = "https://www.ncbi.nlm.nih.gov"
_request_lock = asyncio.Lock()
_last_request = 0.0
_cooldown_until = 0.0

# 部分数据库的摘要包含同一记录的多种标识符。其余数据库使用 Entrez UID。
_ACCESSIONS = {
    "assembly": ("AssemblyAccession", "Synonym/Genbank", "Synonym/RefSeq"),
    "bioproject": ("Project_Acc",), "biosample": ("Accession",),
    "cdd": ("Accession",), "clinvar": ("accession_version", "accession"),
    "gds": ("Accession",), "gtr": ("Accession",), "mesh": ("DS_MeSHUI",),
    "nuccore": ("AccessionVersion", "Caption"), "protein": ("AccessionVersion", "Caption"),
    "proteinclusters": ("ACCN",),
    "seqannot": ("Caption",), "structure": ("PdbAcc",),
}
_TITLE_FIELDS = (
    "Title", "title", "Project_Title", "ScientificName", "Description", "AssemblyName", "Organism_Name",
    "TestName", "AssayName", "SynonymList/string", "IUPACName", "CurrentSourceName",
    "DS_MeshTerms/string", "TitleMainSort", "PdbDescr", "Definition", "TITLE",
    "iname", "AnnotName", "DispProductName/string", "d_study_results/d_study_name",
    "d_document_results/d_document_name", "SPDI", "Name", "SV", "ST", "key",
)
_METADATA_FIELDS = (
    "Description", "Summary", "summary", "Project_Description", "Abstract",
    "ScientificName", "CommonName", "Rank", "Organism", "Organism_Name", "taxon",
    "Chromosome", "Name", "Taxid", "TaxId", "AssemblyAccession", "AssemblyStatus",
    "AssemblyName", "BioSampleAccn", "BioSample", "BioProject", "Project_Data_Type",
    "Project_Type", "Date", "CreateDate", "UpdateDate", "PubDate", "FullJournalName",
    "chr_sort", "protein_change", "GPL", "n_samples", "entryType",
    "MolecularFormula", "MolecularWeight", "Slen", "MolType", "SourceDb",
    "PdbDescr", "ExpMethod", "Resolution", "Definition", "DS_ScopeNote",
    "Submitter_Organization", "Organization", "Infraspecies", "Package", "icode",
    "country", "coltype", "SourceName", "TitleMainSort", "source", "platform",
    "pvalue_string", "AssayDescription", "AssayName", "DispMethod", "ServiceCount",
    "TestType", "Offerer", "Institution", "Status", "InChIKey", "Title", "OBJ_TYPE",
    "SV", "ST", "SNP_CLASS", "CHRPOS", "AssaySourceID", "CurrentSourceName",
    "SID", "AID", "CID", "Gene", "Accession", "Path", "NumSequences",
    "DispFamilyAcc", "DispDescription/string", "d_object_type",
    "d_document_results/d_document_description", "d_study_results/d_study_name",
)
_PAGES = {
    "pubmed": "https://pubmed.ncbi.nlm.nih.gov/{uid}/",
    "pmc": "https://pmc.ncbi.nlm.nih.gov/articles/{id}/",
    "gene": _NCBI + "/gene/{uid}", "bioproject": _NCBI + "/bioproject/{id}",
    "biosample": _NCBI + "/biosample/{id}", "protein": _NCBI + "/protein/{id}",
    "nuccore": _NCBI + "/nuccore/{id}", "snp": _NCBI + "/snp/{id}",
    "gds": _NCBI + "/geo/query/acc.cgi?acc={id}",
    "taxonomy": _NCBI + "/Taxonomy/Browser/wwwtax.cgi?id={uid}",
    "assembly": _NCBI + "/datasets/genome/{id}/",
    "clinvar": _NCBI + "/clinvar/variation/{uid}/",
    "medgen": _NCBI + "/medgen/{uid}", "mesh": _NCBI + "/mesh/{uid}",
    "structure": _NCBI + "/Structure/mmdb/mmdbsrv.cgi?uid={uid}",
    "nlmcatalog": _NCBI + "/nlmcatalog/{uid}",
    "cdd": _NCBI + "/Structure/cdd/cddsrv.cgi?uid={uid}",
    "pccompound": "https://pubchem.ncbi.nlm.nih.gov/compound/{uid}",
    "pcsubstance": "https://pubchem.ncbi.nlm.nih.gov/substance/{uid}",
    "pcassay": "https://pubchem.ncbi.nlm.nih.gov/bioassay/{uid}",
}
_PREFIXES = {
    "pubmed": "PMID", "gene": "GeneID", "taxonomy": "TaxID", "pmc": "PMC",
    "snp": "rs", "pccompound": "CID", "pcassay": "AID", "pcsubstance": "SID",
}
_ACCESSION_FIELDS = {
    "assembly": "ASAC", "bioproject": "PRJA", "biosample": "ACCN",
    "sra": "ACCN", "gds": "ACCN", "nuccore": "ACCN", "protein": "ACCN",
    "clinvar": "VACC", "gtr": "GTRACC", "mesh": "MHUI", "structure": "ACCN",
}


def _database(database: str) -> str:
    if database == "nucleotide":
        return "nuccore"
    if database not in DATABASES:
        raise ValueError(f"未知 NCBI 数据库 {database}")
    return database


async def _request(client: httpx.AsyncClient, action: str, **params: str) -> httpx.Response:
    # 官方匿名额度为每秒三次。搜索和读取摘要共用额度。
    global _last_request, _cooldown_until
    for attempt in range(4):
        async with _request_lock:
            while (delay := max(_last_request + 0.35, _cooldown_until) - time.monotonic()) > 0:
                if delay > 20:
                    raise RuntimeError(f"NCBI 已限流，请在 {int(delay) + 1} 秒后重试")
                await asyncio.sleep(delay)
            _last_request = time.monotonic()
        response = await client.get(_BASE + action + ".fcgi", params={**params, "tool": "biodata_search_mcp"})
        if response.status_code != 429:
            response.raise_for_status()
            return response
        delay = 2 ** attempt
        retry_after = response.headers.get("Retry-After", "").strip()
        if retry_after.isdigit():
            delay = max(delay, int(retry_after))
        elif retry_after:
            deadline = parsedate_to_datetime(retry_after).timestamp()
            delay = max(delay, deadline - time.time())
        _cooldown_until = max(_cooldown_until, time.monotonic() + delay)
        if attempt == 3 or delay > 20:
            response.raise_for_status()
    raise RuntimeError("NCBI 请求未完成")


async def _ids(client: httpx.AsyncClient, db: str, term: str, limit: int, offset: int) -> tuple[int, list[str]]:
    response = await _request(client, "esearch", db=db, term=term, retmode="json",
                              retmax=str(limit), retstart=str(offset))
    payload = response.json()
    result = payload.get("esearchresult", {})
    issue = payload.get("error") or result.get("ERROR") or result.get("errorlist", {}).get("fieldsnotfound")
    if issue:
        raise ValueError(f"NCBI {db} 检索错误：{issue}")
    if "idlist" not in result or "count" not in result:
        raise RuntimeError(f"NCBI {db} 检索响应缺少记录编号或数量")
    return int(result["count"]), result["idlist"]


async def _summaries(client: httpx.AsyncClient, db: str, ids: list[str], *, lookup: bool = False) -> list[ET.Element]:
    if not ids:
        return []
    response = await _request(client, "esummary", db=db, id=",".join(ids), retmode="xml", version="2.0")
    try:
        result = ET.fromstring(response.content)
    except ET.ParseError as error:
        raise RuntimeError(f"NCBI {db} 返回了无法读取的 XML 摘要") from error
    docs = result.findall("./DocumentSummarySet/DocumentSummary")
    error = result.findtext(".//ERROR") or result.findtext(".//Error") or result.findtext(".//error")
    if lookup and error and (error.lower().startswith("invalid uid") or error.lower() == "cannot get document summary"):
        return []
    if error or not docs:
        raise RuntimeError(f"NCBI {db} 未返回完整摘要：{error or '缺少记录'}")
    return docs


def _text(node: ET.Element, path: str) -> str:
    child = node.find(path)
    return "" if child is None else "".join(child.itertext()).strip()


def _sra_record(doc: ET.Element, uid: str, requested: str | None, details: bool) -> dict | None:
    # ESummary 在 ExpXml、Runs 两个字段保存转义后的 XML 片段。
    try:
        root = ET.fromstring(f"<record>{doc.findtext('ExpXml') or ''}{doc.findtext('Runs') or ''}</record>")
    except ET.ParseError as error:
        raise RuntimeError("NCBI SRA 返回了无法读取的实验摘要") from error
    experiment = root.find("Experiment")
    if experiment is None or not experiment.get("acc"):
        raise RuntimeError("NCBI SRA 摘要缺少实验编号")
    item = experiment
    if requested and requested != uid:
        item = next((node for node in root if node.tag in {"Experiment", "Study", "Sample", "Run"}
                     and node.get("acc", "").upper() == requested), None)
        if item is None:
            return None
    accession = item.attrib["acc"]
    title = item.get("name") or _text(root, "Summary/Title") or accession
    url = _NCBI + "/sra/" + quote(accession)
    if item.tag in {"Study", "Sample"}:
        url = "https://www.ncbi.nlm.nih.gov/Traces/study/?acc=" + quote(accession)
    elif item.tag == "Run":
        url = "https://trace.ncbi.nlm.nih.gov/Traces/?view=run_browser&acc=" + quote(accession)
    record = {"id": accession, "title": title, "url": url, "url_type": "record_page"}
    if item.tag == "Experiment":
        record["uid"] = uid
    if details:
        metadata = {"record_type": item.tag, "experiment_accession": experiment.attrib["acc"],
                    "experiment_uid": uid}
        for tag in ("Study", "Sample"):
            related = root.find(tag)
            if related is not None and related.get("acc"):
                metadata[tag] = related.attrib["acc"]
        for name in ("Bioproject", "Biosample", "Library_descriptor/LIBRARY_STRATEGY"):
            if value := _text(root, name):
                metadata[name.split("/")[-1]] = value
        record["metadata"] = metadata
    return record


def _record(db: str, doc: ET.Element, requested: str | None = None) -> dict | None:
    uid = doc.attrib["uid"]
    if db == "sra":
        return _sra_record(doc, uid, requested, requested is not None)
    accessions = [value for path in _ACCESSIONS.get(db, ()) if (value := _text(doc, path))]
    identifier = next(iter(accessions), uid)
    if db in {"snp", "pmc"}:
        identifier = _PREFIXES[db] + uid
    if requested and requested != uid:
        matched = next((acc for acc in accessions if acc.upper() == requested or
                        (db == "assembly" and acc.upper().split(".")[0] == requested)), None)
        if not matched:
            return None
        if db == "assembly":
            identifier = matched
    title = next((value for path in _TITLE_FIELDS if (value := _text(doc, path))), identifier)
    if db in _PAGES:
        url = _PAGES[db].format(id=quote(identifier, safe=""), uid=quote(uid, safe=""))
    else:
        # 有些 Entrez 数据库已迁移网页，ESummary 提供带有确切 UID 的官方记录链接。
        url = _BASE + "esummary.fcgi?" + urlencode({"db": db, "id": uid, "version": "2.0"})
    record = {"id": identifier, "uid": uid, "title": title, "url": url,
              "url_type": "record_page" if db in _PAGES else "api_record"}
    if requested is not None:
        record["metadata"] = {path: value[:3000] for path in _METADATA_FIELDS if (value := _text(doc, path))}
        authors = [_text(author, "Name") for author in doc.findall("./Authors/Author")]
        if authors:
            record["metadata"]["Authors"] = authors[:40]
        for article_id in doc.findall("./ArticleIds/ArticleId"):
            kind, value = _text(article_id, "IdType"), _text(article_id, "Value")
            if kind and value:
                record["metadata"][kind] = value
    return record


async def search(client: httpx.AsyncClient, database: str, query: str, limit: int, offset: int) -> dict:
    db = _database(database)
    total, ids = await _ids(client, db, query, limit, offset)
    docs = await _summaries(client, db, ids)
    by_uid = {doc.attrib["uid"]: doc for doc in docs}
    if set(ids) != set(by_uid):
        raise RuntimeError(f"NCBI {db} 摘要与本页检索编号不一致")
    return {"total": total, "records": [_record(db, by_uid[uid]) for uid in ids]}


async def lookup(client: httpx.AsyncClient, database: str, identifier: str) -> dict | None:
    db = _database(database)
    value = identifier.strip().upper()
    prefix = _PREFIXES.get(db)
    if prefix:
        match = re.fullmatch(rf"{prefix}[:\s]*(\d+)", value, flags=re.IGNORECASE)
        if match:
            value = match.group(1)
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,127}", value):
        raise ValueError("NCBI 编号仅接受字母、数字、下划线、句点和连字符")
    variation = re.fullmatch(r"VCV(\d+)(?:\.\d+)?", value) if db == "clinvar" else None
    if variation or value.isdigit() or db in {"nuccore", "protein"}:
        # 直接读取支持历史 UID 和旧版序列，部分记录已从 ESearch 索引移出。
        uid = str(int(variation[1])) if variation else value
        docs = await _summaries(client, db, [uid], lookup=True)
    else:
        term = f'"{value}"'
        if field := _ACCESSION_FIELDS.get(db):
            term += f"[{field}]"
        _, ids = await _ids(client, db, term, 10, 0)
        docs = await _summaries(client, db, ids)
    for doc in docs:
        match = _record(db, doc, requested=value)
        if match is not None:
            return match
    return None
