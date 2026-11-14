import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from reading_validator import ProbeFailure, Response
from shuyuans_harvester import (ShuyuansHarvester, change_signature, parse_collection,
                                parse_listing_page, parse_updated_at)
from source_store import SourceStore
from test_maintenance_v2 import source

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def entry(collection_id='1330', title='合集', when='3小时前', size=794):
    return (f'<div class="layui-col-xs12"><div class="ylist" style="padding-left: 5px;">'
            f'<h2><a href="/yuedu/shuyuans/content/id/{collection_id}.html">{title}</a>'
            f'<p class="m-right" style="top: 3px;">{when}</p></h2>'
            f'<span class="layui-badge-rim">用户: someone</span>'
            f'<span class="layui-badge-rim">源数量:{size}</span></div></div>')


def page(entries, last_page=1):
    body = ''.join(entry(**item) for item in entries)
    return (f'<html><body>{body}'
            f'<a href="/yuedu/shuyuans/index.html?page={last_page}">{last_page}</a></body></html>')


class UpdatedLabelTests(unittest.TestCase):
    def test_relative_labels_are_measured_from_now(self):
        self.assertEqual(parse_updated_at('3小时前', NOW), NOW - timedelta(hours=3))
        self.assertEqual(parse_updated_at('7天前', NOW), NOW - timedelta(days=7))
        self.assertEqual(parse_updated_at('15分钟前', NOW), NOW - timedelta(minutes=15))

    def test_a_bare_month_and_day_belongs_to_this_year(self):
        self.assertEqual(parse_updated_at('10/24 12:14', NOW), datetime(2026, 10, 24, 12, 14, tzinfo=timezone.utc))

    def test_a_bare_date_in_the_future_belongs_to_last_year(self):
        january = datetime(2026, 1, 3, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_updated_at('12/28 08:00', january),
                         datetime(2025, 12, 28, 8, 0, tzinfo=timezone.utc))

    def test_a_full_date_is_read_as_written(self):
        self.assertEqual(parse_updated_at('2023/04/18', NOW), datetime(2023, 4, 18, tzinfo=timezone.utc))

    def test_an_unknown_label_is_not_guessed(self):
        self.assertIsNone(parse_updated_at('很久以前', NOW))
        self.assertIsNone(parse_updated_at('', NOW))


class ListingTests(unittest.TestCase):
    def test_the_listing_carries_identity_time_and_size(self):
        listings, last = parse_listing_page(page([{'collection_id': '1330', 'size': 794}], last_page=9), NOW)
        self.assertEqual(last, 9)
        self.assertEqual(listings[0].collection_id, '1330')
        self.assertEqual(listings[0].size, 794)
        self.assertEqual(listings[0].updated_at, (NOW - timedelta(hours=3)).isoformat())

    def test_the_signature_survives_a_moving_relative_label(self):
        """'3小时前' today and '1天前' tomorrow are the same collection revision."""
        first, _ = parse_listing_page(page([{'when': '3小时前'}]), NOW)
        later, _ = parse_listing_page(page([{'when': '1天前'}]), NOW + timedelta(days=1))
        self.assertEqual(change_signature(first[0]), change_signature(later[0]))

    def test_a_changed_size_is_a_new_revision(self):
        first, _ = parse_listing_page(page([{'size': 794}]), NOW)
        second, _ = parse_listing_page(page([{'size': 795}]), NOW)
        self.assertNotEqual(change_signature(first[0]), change_signature(second[0]))

    def test_a_page_without_the_block_wrapper_still_lists_collections(self):
        listings, last = parse_listing_page(
            '<a href="/yuedu/shuyuans/content/id/42.html">合集四十二</a>'
            '<a href="/yuedu/shuyuans/index.html?page=3">3</a>', NOW)
        self.assertEqual([item.collection_id for item in listings], ['42'])
        self.assertEqual(listings[0].title, '合集四十二')
        self.assertEqual(last, 3)


class CollectionPayloadTests(unittest.TestCase):
    def test_a_plain_list_is_accepted(self):
        self.assertEqual(len(parse_collection([source()])), 1)

    def test_a_wrapped_payload_is_unwrapped(self):
        self.assertEqual(len(parse_collection({'data': [source()]})), 1)
        self.assertEqual(len(parse_collection({'sources': [source('https://two.example')]})), 1)

    def test_entries_without_a_url_are_dropped(self):
        self.assertEqual(parse_collection([{'bookSourceName': 'broken'}, source()]), [source()])


class Transport:
    def __init__(self, listings=None, payload=None, fail_collection=False):
        self.listings, self.payload = listings or [], payload or [source()]
        self.fail_collection = fail_collection
        self.calls = []

    async def fetch(self, request):
        self.calls.append(request['url'])
        if '/json/' in request['url']:
            if self.fail_collection:
                raise ProbeFailure('rate_limit', 'HTTP 429', 7200)
            return Response(request['url'], json.dumps(self.payload, ensure_ascii=False))
        if not hasattr(self, 'queue'):
            self.queue = list(self.listings)
        return Response(request['url'], self.queue.pop(0) if len(self.queue) > 1 else self.queue[0])


class HarvesterTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_failures_recover_across_restarts(self):
        for failed_page, failures in ((1, 3), (3, 3), (5, 3), (3, 1), (3, 2)):
            with self.subTest(page=failed_page, failures=failures), tempfile.TemporaryDirectory() as tmp, \
                    patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
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
                                    '<a href="/yuedu/shuyuans/content/id/42.html">Book</a>'
                                    '<a href="/yuedu/shuyuans/index.html?page=5">5</a>')

                client = AsyncMock()
                client.fetch.side_effect = fetch
                harvester = ShuyuansHarvester()
                for attempt in range(1, failures + 1):
                    requested.clear()
                    store = SourceStore(Path(tmp))
                    report = await harvester.sync(store, client, fetch_cap=1)
                    state = SourceStore(Path(tmp)).state['providers']['shuyuans']
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
                await harvester.sync(store, client, fetch_cap=1)
                state = SourceStore(Path(tmp)).state['providers']['shuyuans']
                self.assertEqual(requested, list(range(1 if failures == 3 else failed_page, 6)))
                self.assertIsNone(state['catalog_failed_page'])
                self.assertEqual(state['catalog_failure_attempts'], 0)
                self.assertIn('last_full_scan_at', state)

    async def test_shrunk_and_empty_catalogs_reset_without_completion(self):
        for cursor, bound, empty in ((5, 4, False), (5, 5, True), (1, 1, True)):
            with self.subTest(cursor=cursor, bound=bound, empty=empty), tempfile.TemporaryDirectory() as tmp, \
                    patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
                store = SourceStore(Path(tmp))
                store.state['providers']['shuyuans'] = {
                    'catalog': {}, 'scan_cursor': cursor, 'last_page': bound}
                requested = []

                async def fetch(request):
                    if '/json/' in request['url']:
                        return Response(request['url'], json.dumps([source()]))
                    current = int(request['url'].split('?page=')[-1]) if '?page=' in request['url'] else 1
                    requested.append(current)
                    return Response(request['url'], '<html></html>' if empty else
                                    '<a href="/yuedu/shuyuans/content/id/42.html">Book</a>'
                                    '<a href="/yuedu/shuyuans/index.html?page=4">4</a>')

                client = AsyncMock()
                client.fetch.side_effect = fetch
                harvester = ShuyuansHarvester()
                report = await harvester.sync(store, client, fetch_cap=1)
                state = SourceStore(Path(tmp)).state['providers']['shuyuans']
                self.assertEqual(requested, [cursor] if empty else [1, 2, 3, 4])
                self.assertIn('catalog_error', report)
                self.assertEqual(state['scan_cursor'], 1)
                self.assertNotIn('last_full_scan_at', state)
                self.assertEqual(state.get('stalled_scans', 0), int(cursor == 1))
                empty = False
                requested.clear()
                await harvester.sync(SourceStore(Path(tmp)), client, fetch_cap=1)
                self.assertEqual(requested, [1, 2, 3, 4])
                self.assertIn('last_full_scan_at', SourceStore(Path(tmp)).state['providers']['shuyuans'])

    async def test_skip_persists_before_deadline_and_download_only_keeps_counters(self):
        elapsed = 0
        failing = True
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.time') as clock, \
                patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            clock.monotonic.side_effect = lambda: elapsed
            store = SourceStore(Path(tmp))
            store.state['providers']['shuyuans'] = {
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
                                '<a href="/yuedu/shuyuans/content/id/42.html">Book</a>'
                                '<a href="/yuedu/shuyuans/index.html?page=5">5</a>')

            client.fetch.side_effect = fetch
            harvester = ShuyuansHarvester()
            for _ in range(2):
                report = await harvester.sync(store, client, scan=False, save=False, deadline=100)
                self.assertEqual((report['scan_cursor'], report['catalog_failure_attempts'], report['stalled_scans']),
                                 (3, 2, 2))
            elapsed = 100
            report = await harvester.sync(store, client, save=False, deadline=100)
            self.assertEqual(report['catalog_failure_attempts'], 2)
            self.assertEqual(report['stalled_scans'], 2)
            self.assertEqual((Path(tmp) / 'state.json').read_bytes(), before)
            elapsed = 0
            report = await harvester.sync(SourceStore(Path(tmp)), client, deadline=100)
            state = SourceStore(Path(tmp)).state['providers']['shuyuans']
            self.assertEqual(report['skipped_page'], 3)
            self.assertEqual(report['catalog_error'], 'TimeoutError')
            self.assertEqual(report['fetched_ids'], 1)
            self.assertEqual((state['scan_cursor'], state['stalled_scans']), (4, 0))
            self.assertTrue(state['scan_incomplete'])
            self.assertEqual(state['last_full_scan_at'], '2000-01-01T00:00:00+00:00')
            failing = False
            await harvester.sync(SourceStore(Path(tmp)), client, deadline=200)
            state = SourceStore(Path(tmp)).state['providers']['shuyuans']
            self.assertEqual(state['scan_cursor'], 1)
            self.assertFalse(state['scan_incomplete'])
            self.assertEqual(state['last_full_scan_at'], '2000-01-01T00:00:00+00:00')

    async def test_listing_rate_limit_preserves_retry_allowance(self):
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            store.state['providers']['shuyuans'] = {
                'catalog': {'42': {'title': 'Book'}}, 'scan_cursor': 3, 'last_page': 5,
                'catalog_failed_page': 3, 'catalog_failure_attempts': 2, 'stalled_scans': 1}
            client = AsyncMock()
            client.fetch.side_effect = ProbeFailure('rate_limit', 'HTTP 429', 7200)
            harvester = ShuyuansHarvester()
            report = await harvester.sync(store, client)
            self.assertEqual((report['scan_cursor'], report['catalog_failure_attempts']), (3, 2))
            self.assertEqual(report['stalled_scans'], 2)
            self.assertEqual(report['fetched_ids'], 0)
            self.assertNotIn('skipped_page', report)
            for _ in range(2):
                again = await harvester.sync(SourceStore(Path(tmp)), client)
                self.assertEqual(again['skipped'], 'rate_limit')
                self.assertEqual((again['catalog_failure_attempts'], again['stalled_scans']), (2, 2))
            client.fetch.assert_awaited_once()

    async def test_successful_single_page_scans_do_not_stall(self):
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            client = Transport(listings=[page([{'collection_id': '42'}])])
            for _ in range(2):
                report = await ShuyuansHarvester().sync(SourceStore(Path(tmp)), client, fetch_cap=1)
                self.assertEqual(report['scan_cursor'], 1)
                self.assertEqual(report['stalled_scans'], 0)
                self.assertIn('last_full_scan_at', SourceStore(Path(tmp)).state['providers']['shuyuans'])

    async def test_only_collections_from_the_last_year_are_fetched(self):
        fresh = page([{'collection_id': '1', 'when': '2天前', 'size': 100}])
        old = page([{'collection_id': '2', 'when': '2023/04/18', 'size': 100}])
        transport = Transport(listings=[fresh + old])
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            report = await ShuyuansHarvester(request_delay=1).sync(store, transport, fetch_cap=5)

            self.assertEqual(report['fetched_ids'], 1)
            self.assertEqual(report['stale_skipped'], 1)
            self.assertEqual(list(store.state['providers']['shuyuans']['catalog']), ['1'])

    async def test_intake_stops_once_the_run_has_added_enough_new_sources(self):
        listing = page([{'collection_id': '1', 'size': 100}, {'collection_id': '2', 'size': 100}])

        class Collections:
            def __init__(self):
                self.fetched = []

            async def fetch(self, request):
                if '/json/' not in request['url']:
                    return Response(request['url'], listing)
                self.fetched.append(request['url'])
                name = request['url'].rsplit('/', 1)[1].split('.')[0]
                return Response(request['url'], json.dumps(
                    [source(f'https://{name}-{index}.example') for index in range(6)]))

        transport = Collections()
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            report = await ShuyuansHarvester(request_delay=1, min_sources=5, max_new_sources=3).sync(
                store, transport, fetch_cap=5)

            # One collection always lands, then intake waits for the next run: a
            # collection carries more sources than the probe budget can read.
            self.assertEqual(report['fetched_ids'], 1)
            self.assertEqual(report['deferred_collections'], 1)
            self.assertEqual(store.manifest['count'], 6)

    async def test_a_tiny_collection_is_skipped(self):
        transport = Transport(listings=[page([{'collection_id': '1', 'size': 3}])])
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            report = await ShuyuansHarvester(min_sources=5).sync(store, transport, fetch_cap=5)

            self.assertEqual(report['fetched_ids'], 0)
            self.assertEqual(report['stale_skipped'], 1)

    async def test_sources_are_ingested_once_and_counted(self):
        transport = Transport(listings=[page([{'collection_id': '1330', 'size': 2}])],
                              payload=[source(), source('https://two.example')])
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            report = await ShuyuansHarvester(min_sources=1).sync(store, transport, fetch_cap=5)

            self.assertEqual(report['sources'], 2)
            self.assertEqual(report['new_versions'], 2)
            self.assertEqual(SourceStore(Path(tmp)).manifest['count'], 2)

    async def test_an_unchanged_collection_is_not_downloaded_again(self):
        transport = Transport(listings=[page([{'collection_id': '1330', 'when': '3小时前'}])])
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            harvester = ShuyuansHarvester(refetch_days=7)
            await harvester.sync(store, transport, fetch_cap=5)
            downloads = sum(1 for url in transport.calls if '/json/' in url)

            await harvester.sync(SourceStore(Path(tmp)), transport, fetch_cap=5)

            self.assertEqual(sum(1 for url in transport.calls if '/json/' in url), downloads)

    async def test_an_updated_collection_is_downloaded_again(self):
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            harvester = ShuyuansHarvester(refetch_days=7)
            await harvester.sync(store, Transport(listings=[page([{'collection_id': '1', 'when': '3小时前'}])]),
                                 fetch_cap=5)
            transport = Transport(listings=[page([{'collection_id': '1', 'when': '2天前'}])])
            await harvester.sync(SourceStore(Path(tmp)), transport, fetch_cap=5)

            self.assertEqual(sum(1 for url in transport.calls if '/json/' in url), 1)

    async def test_a_rate_limit_sets_a_cooldown_that_stops_downloads(self):
        transport = Transport(listings=[page([{'collection_id': '1'}])], fail_collection=True)
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            harvester = ShuyuansHarvester()
            report = await harvester.sync(store, transport, fetch_cap=5)
            self.assertEqual(report['failed'], 1)
            self.assertIn('cooldown_until', store.state['providers']['shuyuans'])

            again = await harvester.sync(store, transport, fetch_cap=5)

            self.assertEqual(again['skipped'], 'rate_limit')

    async def test_the_deadline_stops_the_download_loop(self):
        transport = Transport(listings=[page([{'collection_id': str(index)} for index in range(5)])])
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()), \
                patch('shuyuans_harvester.time.monotonic', side_effect=[0, 0, 1e9, 5e9]):
            store = SourceStore(Path(tmp))
            report = await ShuyuansHarvester().sync(store, transport, fetch_cap=5, deadline=100)

            self.assertLessEqual(report['fetched_ids'], 2)

    async def test_a_completed_scan_restarts_from_the_first_page(self):
        transport = Transport(listings=[page([{'collection_id': '1'}], last_page=3)])
        with tempfile.TemporaryDirectory() as tmp, patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()):
            store = SourceStore(Path(tmp))
            await ShuyuansHarvester().sync(store, transport, fetch_cap=1)
            state = store.state['providers']['shuyuans']

            self.assertEqual(state['scan_cursor'], 1)
            self.assertIn('last_full_scan_at', state)

    async def test_an_interrupted_scan_resumes_from_its_cursor(self):
        pages = [page([{'collection_id': '1'}], last_page=3), page([{'collection_id': '2'}], last_page=3)]
        clock = iter([0, 0, 0] + [10_000] * 20)
        with tempfile.TemporaryDirectory() as tmp, \
                patch('shuyuans_harvester.asyncio.sleep', new=AsyncMock()), \
                patch('shuyuans_harvester.time.monotonic', side_effect=lambda: next(clock, 10_000)):
            store = SourceStore(Path(tmp))
            await ShuyuansHarvester().sync(store, Transport(listings=list(pages)), fetch_cap=1, deadline=100)
            state = store.state['providers']['shuyuans']

            self.assertEqual(state['scan_cursor'], 2)
            self.assertEqual(state['last_page'], 3)


if __name__ == '__main__':
    unittest.main()
