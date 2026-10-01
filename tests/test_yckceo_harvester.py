import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from reading_validator import Response, ProbeFailure
from source_store import SourceStore
from yckceo_harvester import YckceoHarvester, parse_listing_page
from test_maintenance_v2 import source


class CatalogTransport:
    def __init__(self, label='today', payload=None, fail=False):
        self.label, self.payload, self.fail = label, payload or [source()], fail

    async def fetch(self, request):
        if '/json/' in request['url']:
            if self.fail:
                raise ProbeFailure('rate_limit', 'HTTP 429')
            return Response(request['url'], json.dumps(self.payload))
        return Response(request['url'], f'<div class="ylist"><h2><a href="/yuedu/shuyuan/content/id/42.html">Book</a><p>{self.label}</p></h2></div>')


class HarvesterTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_id_can_receive_a_new_revision(self):
        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            harvester = YckceoHarvester()
            await harvester.sync(store, CatalogTransport(), fetch_cap=1)
            await harvester.sync(store, CatalogTransport('tomorrow', [{**source(), 'searchUrl': '/new?q={{key}}'}]), fetch_cap=1)
            record = next(SourceStore(Path(tmp)).records())
            self.assertEqual(len(record['versions']), 2)

    async def test_failed_fetch_is_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            await YckceoHarvester().sync(store, CatalogTransport(fail=True), fetch_cap=1)
            entry = SourceStore(Path(tmp)).state['providers']['yckceo']['catalog']['42']
            self.assertNotIn('last_fetched_at', entry)
            self.assertIn('next_fetch_at', entry)

    async def test_download_cap_counts_ids_not_array_members(self):
        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            report = await YckceoHarvester().sync(store, CatalogTransport(payload=[source(), source('https://two.example')]), fetch_cap=1)
            self.assertEqual(report['fetched_ids'], 1)
            self.assertEqual(report['sources'], 2)

    def test_last_page_and_duplicate_ids(self):
        value = '<a href="/yuedu/shuyuan/content/id/42.html">One</a>' * 2
        value += '<a href="/yuedu/shuyuan/index.html?page=58">58</a>'
        entries, last = parse_listing_page(value)
        self.assertEqual([item.source_id for item in entries], ['42'])
        self.assertEqual(last, 58)

    async def test_catalog_rate_limit_stops_downloads_and_persists_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            store.state['providers'] = {'yckceo': {'catalog': {'42': {'title': 'Book'}}, 'scan_cursor': 1}}
            client = AsyncMock()
            client.fetch.side_effect = ProbeFailure('rate_limit', 'HTTP 429', 7200)
            harvester = YckceoHarvester()
            report = await harvester.sync(store, client)
            self.assertEqual(report['fetched_ids'], 0)
            client.fetch.assert_awaited_once()
            loaded = SourceStore(Path(tmp))
            self.assertIn('cooldown_until', loaded.state['providers']['yckceo'])
            report = await harvester.sync(loaded, client)
            self.assertEqual(report['skipped'], 'rate_limit')
            client.fetch.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
