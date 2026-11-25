import asyncio
import os
import shutil
import sysconfig
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    assert not result.isError, (name, result.content)
    assert isinstance(result.structuredContent, dict), (name, result.content)
    return result.structuredContent


def check_record(database, record):
    assert record['id'] and record['title'].strip(), (database, record)
    url = urlsplit(record['url'])
    assert url.scheme == 'https', record['url']
    if database == 'ngdc:omix':
        assert url.hostname == 'ngdc.cncb.ac.cn', record['url']
        assert url.path.rstrip('/') == '/omix/release/' + record['id'], record['url']
    else:
        assert database == 'ncbi:pubmed', database
        assert url.hostname == 'pubmed.ncbi.nlm.nih.gov', record['url']
        assert url.path.strip('/') == record['id'], record['url']


async def main():
    env_keys = {'NCBI_API_KEY', 'NCBI_EMAIL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                'NO_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'PYTHONPATH'}
    environment = {key: value for key, value in os.environ.items() if key.upper() in env_keys}
    environment['BIODATA_SEARCH_PROVIDERS'] = 'uniprot,rcsb'
    command = shutil.which('biodata-search-mcp', path=sysconfig.get_path('scripts'))
    assert command, '找不到已安装的 biodata-search-mcp 启动命令'
    server = StdioServerParameters(command=command, env=environment)
    async with stdio_client(server) as (reader, writer), ClientSession(
        reader, writer, read_timeout_seconds=timedelta(seconds=90)
    ) as session:
        handshake = await session.initialize()
        assert handshake.serverInfo.name == 'BioData Search', handshake.serverInfo
        advertised = {tool.name for tool in (await session.list_tools()).tools}
        assert {'search_records', 'get_record', 'list_databases'} <= advertised, advertised

        databases = ['ngdc:omix', 'ncbi:pubmed']
        catalog = await call(session, 'list_databases', {})
        assert set(databases) <= {item['database'] for item in catalog['databases']}
        assert catalog['default_databases'] == ['uniprot:uniprotkb', 'rcsb:entry'], catalog
        filtered = await call(session, 'list_databases', {'providers': ['EuropePMC']})
        assert [item['database'] for item in filtered['databases']] == ['europepmc:articles'], filtered

        for tool, arguments in [
            ('search_records', {'query': 'rice', 'limit': 0}),
            ('search_records', {'query': 'rice', 'offset': -1}),
            ('search_records', {'query': 'rice', 'providers': []}),
            ('search_records', {'query': 'rice', 'providers': ['invalid']}),
            ('search_records', {'query': 'rice', 'providers': ['rcsb'], 'databases': ['ncbi:gds']}),
            ('get_record', {'identifier': '   '}),
            ('get_record', {'identifier': '4530'}),
        ]:
            rejected = await session.call_tool(tool, arguments)
            assert rejected.isError, (tool, arguments, rejected.content)

        search = await call(session, 'search_records', {'query': 'rice', 'databases': databases, 'limit': 1})
        groups = {group['database']: group for group in search['results']}
        assert set(groups) == set(databases), search
        for database, group in groups.items():
            assert 'error' not in group, group
            assert len(group['records']) == 1, group
            check_record(database, group['records'][0])

        identifier = groups['ncbi:pubmed']['records'][0]['id']
        detail = await call(session, 'get_record', {'database': 'ncbi:pubmed', 'identifier': identifier})
        assert detail['database'] == 'ncbi:pubmed' and detail['record']['id'] == identifier, detail
        check_record(detail['database'], detail['record'])

        scoped = await call(session, 'search_records', {'query': 'hemoglobin', 'limit': 1})
        assert [group['database'] for group in scoped['results']] == catalog['default_databases'], scoped
        literature = await call(session, 'search_records', {'query': 'hemoglobin', 'providers': ['europepmc'], 'limit': 1})
        assert [group['database'] for group in literature['results']] == ['europepmc:articles'], literature
        for group in [*scoped['results'], *literature['results']]:
            assert 'error' not in group and group['total'] > 0 and len(group['records']) == 1, group
            hit = group['records'][0]
            item = await call(session, 'get_record', {'identifier': hit['id'], 'database': group['database']})
            assert item['record']['id'] == hit['id'] and item['record']['url'] == hit['url'], (group, item)

        for accession, database, host in [('P05067', 'uniprot:uniprotkb', 'www.uniprot.org'),
                                          ('4HHB', 'rcsb:entry', 'www.rcsb.org'),
                                          ('MED:29535343', 'europepmc:articles', 'europepmc.org')]:
            item = await call(session, 'get_record', {'identifier': accession})
            assert item['database'] == database and urlsplit(item['record']['url']).hostname == host, item

        omix = await call(session, 'get_record', {'identifier': 'OMIX018582'})
        assert omix['database'] == 'ngdc:omix' and omix['record']['id'] == 'OMIX018582', omix
        check_record(omix['database'], omix['record'])
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            page = await client.get(omix['record']['url'])
            page.raise_for_status()
            assert 'text/html' in page.headers['content-type'], page.url
            assert omix['record']['id'] in page.text and omix['record']['title'] in page.text, page.url

    print('MCP 协议验收通过。已验证跨库检索、编号查询、原始记录网页和参数校验。')


if __name__ == '__main__':
    asyncio.run(main())
