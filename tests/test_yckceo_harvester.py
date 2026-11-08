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
    async def test_daily_scans_retry_without_overwriting_full_scan_cursor(self):
        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            store.state['providers']['yckceo'] = {'catalog': {}, 'scan_cursor': 5, 'last_page': 6}
            store.save()
            requested = []

            async def fetch(request):
                current = int(request['url'].split('?page=')[-1]) if '?page=' in request['url'] else 1
                requested.append(current)
                if current == 2:
                    raise ValueError('broken listing')
                return Response(request['url'], '<a href="/yuedu/shuyuan/content/id/42.html">Book</a>'
                                '<a href="/yuedu/shuyuan/index.html?page=6">6</a>')

            client = AsyncMock()
            client.fetch.side_effect = fetch
            for attempt in range(1, 4):
                requested.clear()
                report = await YckceoHarvester().sync(SourceStore(Path(tmp)), client, daily_pages=3, fetch_cap=0)
                self.assertEqual(report['scan_cursor'], 5)
                self.assertEqual(requested, [1, 2] if attempt < 3 else [1, 2, 3])
                self.assertEqual(report['catalog_failure_attempts'], attempt if attempt < 3 else 0)
            self.assertEqual(report['skipped_page'], 2)
            self.assertNotIn('last_full_scan_at', SourceStore(Path(tmp)).state['providers']['yckceo'])

    async def test_catalog_failures_recover_across_restarts(self):
        for failed_page, failures in ((1, 3), (3, 3), (5, 3), (3, 1), (3, 2)):
            with self.subTest(page=failed_page, failures=failures), tempfile.TemporaryDirectory() as tmp, \
                    patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
                requested = []
                failing = True

                async def fetch(request):
                    if '/json/' in request['url']:
                        return Response(request['url'], json.dumps([source()]))
                    current = int(request['url'].split('?page=')[-1]) if '?page=' in request['url'] else 1
                    requested.append(current)
                    if failing and current == failed_page:
                        raise ProbeFailure('http_error', 'HTTP 404')
                    return Response(request['url'],
                                    '<a href="/yuedu/shuyuan/content/id/42.html">Book</a>'
                                    '<a href="/yuedu/shuyuan/index.html?page=5">5</a>')

                client = AsyncMock()
                client.fetch.side_effect = fetch
                harvester = YckceoHarvester()
                for attempt in range(1, failures + 1):
                    requested.clear()
                    store = SourceStore(Path(tmp))
                    report = await harvester.sync(store, client, full_scan=True, fetch_cap=1)
                    state = SourceStore(Path(tmp)).state['providers']['yckceo']
                    self.assertIn('HTTP 404', report['catalog_error'])
                    self.assertEqual(report['catalog_error_page'], failed_page)
                    self.assertNotIn('last_full_scan_at', state)
                    if attempt < 3:
                        self.assertEqual(state['scan_cursor'], failed_page)
                        self.assertEqual(state['catalog_failed_page'], failed_page)
                        self.assertEqual(state['catalog_failure_attempts'], attempt)
                        self.assertEqual(state['stalled_scans'], attempt if failed_page == 1 else attempt - 1)
                    else:
                        self.assertEqual(requested, list(range(failed_page, 6)))
                        self.assertEqual(report['skipped_page'], failed_page)
                        self.assertEqual(state['scan_cursor'], 1)
                        self.assertEqual(state['catalog_failure_attempts'], 0)
                        self.assertEqual(state['stalled_scans'], 0)
                        self.assertFalse(state['scan_incomplete'])
                failing = False
                requested.clear()
                store = SourceStore(Path(tmp))
                await harvester.sync(store, client, full_scan=True, fetch_cap=1)
                state = SourceStore(Path(tmp)).state['providers']['yckceo']
                self.assertEqual(requested, list(range(1 if failures == 3 else failed_page, 6)))
                self.assertIsNone(state['catalog_failed_page'])
                self.assertEqual(state['catalog_failure_attempts'], 0)
                self.assertIn('last_full_scan_at', state)

    async def test_shrunk_and_empty_catalogs_reset_without_completion(self):
        for cursor, bound, empty in ((5, 4, False), (5, 5, True), (1, 1, True)):
            with self.subTest(cursor=cursor, bound=bound, empty=empty), tempfile.TemporaryDirectory() as tmp, \
                    patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
                store = SourceStore(Path(tmp))
                store.state['providers']['yckceo'] = {
                    'catalog': {}, 'scan_cursor': cursor, 'last_page': bound}
                requested = []

                async def fetch(request):
                    if '/json/' in request['url']:
                        return Response(request['url'], json.dumps([source()]))
                    current = int(request['url'].split('?page=')[-1]) if '?page=' in request['url'] else 1
                    requested.append(current)
                    return Response(request['url'], '<html></html>' if empty else
                                    '<a href="/yuedu/shuyuan/content/id/42.html">Book</a>'
                                    '<a href="/yuedu/shuyuan/index.html?page=4">4</a>')

                client = AsyncMock()
                client.fetch.side_effect = fetch
                harvester = YckceoHarvester()
                report = await harvester.sync(store, client, full_scan=True, fetch_cap=1)
                state = SourceStore(Path(tmp)).state['providers']['yckceo']
                self.assertEqual(requested, [cursor] if empty else [1, 2, 3, 4])
                self.assertIn('catalog_error', report)
                self.assertEqual(state['scan_cursor'], 1)
                self.assertNotIn('last_full_scan_at', state)
                self.assertEqual(state.get('stalled_scans', 0), int(cursor == 1))
                empty = False
                requested.clear()
                await harvester.sync(SourceStore(Path(tmp)), client, full_scan=True, fetch_cap=1)
                self.assertEqual(requested, [1, 2, 3, 4])
                self.assertIn('last_full_scan_at', SourceStore(Path(tmp)).state['providers']['yckceo'])

    async def test_skip_persists_before_deadline_and_download_only_keeps_counters(self):
        elapsed = 0
        failing = True
        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.time') as clock, \
                patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            clock.monotonic.side_effect = lambda: elapsed
            store = SourceStore(Path(tmp))
            store.state['providers']['yckceo'] = {
                'catalog': {'42': {'title': 'Book'}}, 'scan_cursor': 3, 'last_page': 5,
                'catalog_failed_page': 3, 'catalog_failure_attempts': 2, 'stalled_scans': 2,
                'last_full_scan_at': '2000-01-01T00:00:00+00:00'}
            store.save()
            before = (Path(tmp) / 'state.json').read_bytes()
            client = AsyncMock()

            async def fetch(request):
                nonlocal elapsed
                if '/json/' in request['url']:
                    return Response(request['url'], json.dumps([source()]))
                elapsed = 60
                if failing:
                    raise TimeoutError()
                return Response(request['url'],
                                '<a href="/yuedu/shuyuan/content/id/42.html">Book</a>'
                                '<a href="/yuedu/shuyuan/index.html?page=5">5</a>')

            client.fetch.side_effect = fetch
            harvester = YckceoHarvester()
            for _ in range(2):
                report = await harvester.sync(store, client, full_scan=True, scan=False, save=False, deadline=100)
                self.assertEqual((report['scan_cursor'], report['catalog_failure_attempts'], report['stalled_scans']),
                                 (3, 2, 2))
            elapsed = 100
            report = await harvester.sync(store, client, full_scan=True, save=False, deadline=100)
            self.assertEqual(report['catalog_failure_attempts'], 2)
            self.assertEqual(report['stalled_scans'], 2)
            self.assertEqual((Path(tmp) / 'state.json').read_bytes(), before)
            elapsed = 0
            report = await harvester.sync(SourceStore(Path(tmp)), client, full_scan=True, deadline=100)
            state = SourceStore(Path(tmp)).state['providers']['yckceo']
            self.assertEqual(report['skipped_page'], 3)
            self.assertEqual(report['catalog_error'], 'TimeoutError')
            self.assertEqual(report['fetched_ids'], 1)
            self.assertEqual((state['scan_cursor'], state['stalled_scans']), (4, 0))
            self.assertTrue(state['scan_incomplete'])
            self.assertEqual(state['last_full_scan_at'], '2000-01-01T00:00:00+00:00')
            failing = False
            await harvester.sync(SourceStore(Path(tmp)), client, full_scan=True, deadline=200)
            state = SourceStore(Path(tmp)).state['providers']['yckceo']
            self.assertEqual(state['scan_cursor'], 1)
            self.assertFalse(state['scan_incomplete'])
            self.assertEqual(state['last_full_scan_at'], '2000-01-01T00:00:00+00:00')

    async def test_listing_rate_limit_preserves_retry_allowance(self):
        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            store.state['providers']['yckceo'] = {
                'catalog': {'42': {'title': 'Book'}}, 'scan_cursor': 3, 'last_page': 5,
                'catalog_failed_page': 3, 'catalog_failure_attempts': 2, 'stalled_scans': 1}
            client = AsyncMock()
            client.fetch.side_effect = ProbeFailure('rate_limit', 'HTTP 429', 7200)
            harvester = YckceoHarvester()
            report = await harvester.sync(store, client, full_scan=True)
            self.assertEqual((report['scan_cursor'], report['catalog_failure_attempts']), (3, 2))
            self.assertEqual(report['stalled_scans'], 2)
            self.assertEqual(report['fetched_ids'], 0)
            self.assertNotIn('skipped_page', report)
            for _ in range(2):
                again = await harvester.sync(SourceStore(Path(tmp)), client, full_scan=True)
                self.assertEqual(again['skipped'], 'rate_limit')
                self.assertEqual((again['catalog_failure_attempts'], again['stalled_scans']), (2, 2))
            client.fetch.assert_awaited_once()

    async def test_successful_single_page_scans_do_not_stall(self):
        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            for _ in range(2):
                report = await YckceoHarvester().sync(SourceStore(Path(tmp)), CatalogTransport(), full_scan=True, fetch_cap=1)
                self.assertEqual(report['scan_cursor'], 1)
                self.assertEqual(report['stalled_scans'], 0)
                self.assertIn('last_full_scan_at', SourceStore(Path(tmp)).state['providers']['yckceo'])

    async def test_long_catalog_scan_leaves_time_to_download_and_resumes(self):
        elapsed = 0

        async def fetch(request):
            nonlocal elapsed
            if '/json/' in request['url']:
                return Response(request['url'], json.dumps([source()]))
            elapsed += 6
            return Response(request['url'],
                            '<a href="/yuedu/shuyuan/content/id/42.html">Book</a>'
                            '<a href="/yuedu/shuyuan/index.html?page=20">20</a>')

        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.time') as clock, \
                patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            clock.monotonic.side_effect = lambda: elapsed
            store = SourceStore(Path(tmp))
            client = AsyncMock()
            client.fetch.side_effect = fetch
            harvester = YckceoHarvester(request_delay=0)
            report = await harvester.sync(store, client, full_scan=True, fetch_cap=1, deadline=100)
            self.assertGreater(report['pages'], 0)
            self.assertLess(report['pages'], 20)
            self.assertEqual(report['fetched_ids'], 1)
            loaded = SourceStore(Path(tmp))
            state = loaded.state['providers']['yckceo']
            self.assertEqual(state['scan_cursor'], report['pages'] + 1)
            self.assertNotIn('last_full_scan_at', state)
            self.assertEqual(loaded.manifest['count'], 1)
            await harvester.sync(loaded, client, full_scan=True, deadline=300)
            self.assertEqual(state['scan_cursor'], 1)
            self.assertIn('last_full_scan_at', state)

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

    async def test_the_catalog_stops_growing_at_the_configured_limit(self):
        with tempfile.TemporaryDirectory() as tmp, patch('yckceo_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            store.state['providers'] = {'yckceo': {'catalog': {'1': {'title': 'Seen'}}, 'scan_cursor': 1}}
            harvester = YckceoHarvester(max_seen_ids=1)

            report = await harvester.sync(store, CatalogTransport(), fetch_cap=1)

            self.assertTrue(report.get('catalog_capped'))
            self.assertEqual(list(store.state['providers']['yckceo']['catalog']), ['1'])
            self.assertEqual(report['fetched_ids'], 1)


if __name__ == '__main__':
    unittest.main()
