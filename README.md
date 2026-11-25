# BioData Search

BioData Search 在 NGDC、NCBI、Europe PMC、UniProtKB 和 RCSB PDB 中检索公开记录。可以按来源搜索，也可以用编号直接读取详情。结果附有原始记录链接，支持 MCP 客户端和 DeepSeek Harness。

## 在 DeepSeek Harness 中安装

在装有 `uvx` 的环境中运行以下命令，将 npm 插件加入 DSH 的 Web 配置。终端界面可将 `web` 改为 `tui`。[DSH 插件要求与示例](https://github.com/bei7777/dsh-plugin-biodata-search)

```sh
dsh plugin --profile web add dsh-plugin-biodata-search
```

## 连接 MCP 客户端

使用 Python 3.11 或更高版本。在本目录执行以下命令，按 `uv.lock` 安装 `biodata-search-mcp` 及其依赖。

```sh
uv sync --frozen
```

从本目录把插件加入 Codex，然后打开新会话。

```sh
codex mcp add biodata-search -- "$PWD/.venv/bin/biodata-search-mcp"
```

支持 stdio 的其他客户端可以使用下列配置，把 `command` 改为本项目中可执行文件的绝对路径。Windows 对应文件为 `.venv/Scripts/biodata-search-mcp.exe`。查询公开元数据无需 API 密钥。

```json
{
  "mcpServers": {
    "biodata-search": {
      "command": "/absolute/path/to/biodata-search-mcp/.venv/bin/biodata-search-mcp",
      "args": []
    }
  }
}
```

## 查询示例

| 工具 | 入参示例 | 用途 |
| --- | --- | --- |
| `search_records` | `{"query":"rice","databases":["ngdc:omix","ncbi:gds"],"limit":3}` | 同时检索 NGDC 多组学数据与 NCBI GEO，分别返回总数和本页记录 |
| `search_records` | `{"query":"hemoglobin","providers":["europepmc","uniprot","rcsb"],"limit":2}` | 分别检索文献、蛋白质和实验结构，每个来源返回两条 |
| `get_record` | `{"identifier":"OMIX018582"}` | 自动识别 NGDC 登录号，读取黑米代谢组记录及其原始网页 |
| `get_record` | `{"identifier":"4530","database":"ncbi:taxonomy"}` | 读取水稻分类详情。`{"identifier":"TaxID:4530"}` 也指向同一条记录 |
| `get_record` | `{"identifier":"4HHB"}` | 获取 RCSB PDB 的血红蛋白结构元数据和记录网页 |
| `list_databases` | `{"providers":["uniprot","rcsb"]}` | 显示所选来源的数据库。省略参数可列出全部 137 个数据库或检索索引 |

`providers` 可选 `ngdc`、`ncbi`、`europepmc`、`uniprot`、`rcsb`。选择来源后，插件查询这些来源各自的默认库。如果同时传入 `databases`，则仅查询其中列出的数据库，这些数据库须属于所选来源。

省略这两个参数时，插件默认查询 NGDC 的 GSA、OMIX 和 NCBI 的 BioProject、GEO DataSets、PubMed。要保存个人偏好，可在 MCP 客户端的服务器 `env` 配置中设置 `BIODATA_SEARCH_PROVIDERS`，例如 `europepmc,uniprot`。DSH 用户可在启动宿主前设置同名环境变量。显式查询参数优先于默认偏好。关键词原样传给上游，物种可以直接使用学名，例如 `Oryza sativa`。

`limit` 指定每个数据库的返回上限，范围为 1 至 10。读取下一页时，使用该库的 `next_offset` 作为 `offset`。`next_offset` 为空表示这一页已经结束该库的检索。部分数据库请求失败时，`partial` 为 `true`，对应分组含 `error`，其他分组仍可使用。成功执行的详情查询在没有匹配记录时返回 `record: null`。

检索结果中的 `id` 可配合同组的 `database` 传给 `get_record`。详情工具也能识别常用前缀，包括 `GWH`、`CRA`、`CRX`、`CRR`、`OMIX`、`PRJCA`、`SAMC`、`GSE`、`GCA_`、`GCF_`、`PMC`。纯数字有多种含义，需要指定库名或写作 `PMID:编号`、`GeneID:编号`、`TaxID:编号`、`CID:编号`。

## 数据与链接

- NGDC 关键词检索使用 BIG Search 官网的分页接口。`url` 保留索引给出的原始链接。遇到隐藏或缺少原始链接的条目时，`omitted_without_url` 会列出本页省略的数量，总数沿用上游值。GSA 新发布的编号可能晚于官网进入索引。[BIG Search 官网](https://ngdc.cncb.ac.cn/search/)
- GWH 组装、关联项目和样本，以及部分 ICG、IC4R 编号有公开详情接口。可用时，`detail_source` 标明接口名称，`api_url` 指向结构化数据，`url` 指向记录网页。其他 NGDC 详情使用 BIG Search 已索引的记录及属性。[GWH 文档](https://ngdc.cncb.ac.cn/gwh/api_documents) · [ICG 文档](https://ngdc.cncb.ac.cn/icg/api) · [IC4R 文档](https://ngdc.cncb.ac.cn/ic4r/api)
- NCBI 使用 ESearch 查找匹配编号，再用 ESummary 读取记录摘要和元数据。对有已知详情页的库，`url` 指向单条记录网页。部分数据库的 `url` 指向包含该 UID 的官方摘要接口，用 `url_type` 标明。`ncbi:nucleotide` 是 `ncbi:nuccore` 的别名。[NCBI 官方接口说明](https://www.ncbi.nlm.nih.gov/home/develop/api/)
- `europepmc:articles` 检索 Europe PMC 文献元数据，通过来源和编号识别单条记录，例如 `MED:29535343`。指定该库时，也可以传 PMID 或 PMCID。[Europe PMC REST 文档](https://europepmc.org/RestfulWebService)
- `uniprot:uniprotkb` 检索蛋白质名称、基因和物种等条目信息，支持按主登录号读取，例如 `P05067`。[UniProt API 文档](https://www.uniprot.org/help/api)
- `rcsb:entry` 检索实验测定的结构条目。按编号可以读取结构标题、实验方法及已收录的分辨率等信息，例如 `4HHB`。[RCSB 检索接口](https://search.rcsb.org/) · [RCSB 数据接口](https://data.rcsb.org/)

## 验证

在项目目录运行 `uv run --frozen python tests/live_smoke.py`。脚本通过 MCP 客户端实测五个来源的检索和编号查询、来源筛选、用户默认偏好以及原始记录链接。

## 依赖与来源

接口设计参考 PlantOmics Hub 的查询工具和来源记录方式。本项目使用 Python 独立实现。[参考项目](https://github.com/maorong-rong/plant-omics-hub/tree/cb10158040bcebed4df658609e0ec589fe5a17fc)

本项目通过依赖声明安装 MCP Python SDK 1.30.0、HTTPX 0.28.1 和 Pydantic 2.14.4，安装包保留各自许可证。再分发依赖时，MCP SDK 和 Pydantic 的 MIT 许可证要求保留版权和许可声明，HTTPX 的 BSD-3-Clause 要求保留版权声明、许可条件和免责声明，且未经许可不得使用作者名称为衍生产品背书。[MCP SDK 许可证](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/LICENSE) · [HTTPX 许可证](https://github.com/encode/httpx/blob/0.28.1/LICENSE.md) · [Pydantic 许可证](https://github.com/pydantic/pydantic/blob/v2.14.4/LICENSE)
