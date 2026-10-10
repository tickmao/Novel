import asyncio
import json
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from batch_validator import BatchValidator
from daily_maintenance import DailyMaintenance
from source_store import SourceStore, read_json, utcnow
from source_health import VALIDATOR_VERSION
from test_inventory_v2 import seed, ready_result
from test_maintenance_v2 import source
from publication_fixture import enable_test_publication


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.runner = DailyMaintenance(self.root)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_existing_inventory_is_checked_before_collection(self):
        old = self.runner.inventory.store.ingest(source(), {'provider': 'legacy'})
        self.runner.inventory.store.save()
        collecting, validating = asyncio.Event(), asyncio.Event()
        added = []

        async def collect(store, client, **kwargs):
            collecting.set()
            await asyncio.wait_for(validating.wait(), 1)
            added.append(store.ingest(source('https://new.example'), {'provider': 'yckceo'}))
            return {'fetched_ids': 1, 'pending_count': 0}

        async def probe(item, mode='deep'):
            self.assertFalse(collecting.is_set())
            validating.set()
            return {**ready_result(self.runner.inventory.policy), 'mode': mode}

        with patch('daily_maintenance.collect_submissions', new=AsyncMock(return_value={})), \
                patch('daily_maintenance.collect_into', new=AsyncMock(return_value={'files': 0, 'errors': []})), \
                patch('daily_maintenance.YckceoHarvester.sync', side_effect=collect), \
                patch('daily_maintenance.ReadingValidator.probe', side_effect=probe):
            report = await self.runner.maintain(mode='catchup')
        self.assertEqual(report['deep_checked'], 1)
        loaded = SourceStore(self.runner.inventory.store.root)
        self.assertEqual(loaded.get(old[0])['versions'][old[1]]['validation']['status'], 'valid')
        self.assertEqual(loaded.get(added[0][0])['versions'][added[0][1]]['validation']['status'], 'pending')

        with patch('daily_maintenance.ReadingValidator.probe', new=AsyncMock(return_value=ready_result(self.runner.inventory.policy))):
            report = await self.runner.maintain(mode='catchup', collect=False, publish=True)
        self.assertEqual(report['inventory']['healthy_count'], 2)
        self.assertTrue(report['published'])

    async def test_full_inventory_does_not_collect_more_sources(self):
        self.runner.inventory.export_target = 1
        self.runner.inventory.reserve_target = 0
        seed(self.runner.inventory, 1)
        self.runner.inventory.store.save()
        with patch('daily_maintenance.collect_submissions', new=AsyncMock(return_value={})), \
                patch('daily_maintenance.collect_into', new=AsyncMock(return_value={'files': 0, 'errors': []})), \
                patch('daily_maintenance.YckceoHarvester.sync', new=AsyncMock(return_value={})) as collect:
            report = await self.runner.maintain(mode='catchup')
        collect.assert_not_awaited()
        self.assertFalse(report['inventory']['needs_replenishment'])

    async def test_pending_raw_inventory_prevents_collection_and_resumes(self):
        for index in range(3):
            self.runner.inventory.store.ingest(source(f'https://old{index}.example'), {'provider': 'legacy'})
        self.runner.inventory.store.save()
        seen = []

        async def probe(item, mode='deep'):
            seen.append(item['bookSourceUrl'])
            result = ready_result(self.runner.inventory.policy)
            result.update(status='unverified', kind='http_forbidden')
            return result

        with patch('daily_maintenance.DailyMaintenance.collect_sources', new=AsyncMock()) as collect, \
                patch('daily_maintenance.ReadingValidator.probe', side_effect=probe):
            first = await self.runner.maintain(mode='catchup', batch_size=1)
            collect.assert_not_awaited()
            restarted = DailyMaintenance(self.root)
            second = await restarted.maintain(mode='catchup', batch_size=1)
            collect.assert_not_awaited()
            third = await restarted.maintain(mode='catchup', batch_size=1)
            collect.assert_awaited_once()
        self.assertEqual(len(set(seen)), 3)
        self.assertEqual(first['raw_progress']['pending_revisions'], 2)
        self.assertEqual(second['raw_progress']['pending_revisions'], 1)
        self.assertEqual(third['raw_progress']['pending_revisions'], 0)
        self.assertEqual(first['collection_decision']['reason'], 'raw_inventory_pending')
        self.assertEqual(third['collection_decision']['reason'], 'qualified_inventory_shortage')
        self.assertNotIn('deep_results', restarted.inventory.store.state['runs'][-1])
        self.assertEqual(len(third['deep_results']), 1)

    async def test_engine_failure_does_not_unlock_collection(self):
        self.runner.inventory.store.ingest(source(), {'provider': 'legacy'})
        self.runner.inventory.store.save()
        result = {**ready_result(self.runner.inventory.policy), 'status': 'unverified', 'kind': 'engine_unavailable'}
        with patch('daily_maintenance.DailyMaintenance.collect_sources', new=AsyncMock()) as collect, \
                patch('daily_maintenance.ReadingValidator.probe', new=AsyncMock(return_value=result)):
            report = await self.runner.maintain(mode='catchup')
        collect.assert_not_awaited()
        self.assertEqual(report['raw_progress']['pending_revisions'], 1)

    async def test_provider_failure_does_not_stop_validation_or_other_providers(self):
        self.runner.inventory.store.ingest(source(), {'provider': 'legacy'})
        self.runner.inventory.store.save()
        with patch('daily_maintenance.collect_submissions', new=AsyncMock(side_effect=OSError('Feed unavailable'))), \
                patch('daily_maintenance.collect_into', new=AsyncMock(return_value={'files': 0, 'errors': []})) as external, \
                patch('daily_maintenance.YckceoHarvester.sync', new=AsyncMock(return_value={})) as collect, \
                patch('daily_maintenance.ReadingValidator.probe', new=AsyncMock(return_value=ready_result(self.runner.inventory.policy))):
            report = await self.runner.maintain(mode='catchup')
        self.assertEqual(report['inventory']['healthy_count'], 1)
        self.assertIn('error', report['providers']['submissions'])
        collect.assert_awaited_once()
        external.assert_awaited_once()

    async def test_expired_collection_budget_saves_partial_work(self):
        self.runner.inventory.store.save()
        added = []

        async def collect(store, client, **kwargs):
            added.append(store.ingest(source(), {'provider': 'yckceo'}))
            await asyncio.Event().wait()

        with patch('daily_maintenance.collect_submissions', new=AsyncMock(return_value={})), \
                patch('daily_maintenance.collect_into', new=AsyncMock(return_value={'files': 0, 'errors': []})), \
                patch('daily_maintenance.YckceoHarvester.sync', side_effect=collect):
            report = {'providers': {}}
            await self.runner.collect_sources(None, mode='catchup', deadline=time.monotonic() + 0.1,
                                              save=True, fetch_cap=1, backfill_rounds=1, report=report)
        self.assertTrue(report['collection_budget_exhausted'])
        self.assertEqual(report['providers']['yckceo']['status'], 'budget_exhausted')
        loaded = SourceStore(self.runner.inventory.store.root)
        self.assertIn(added[0][1], loaded.get(added[0][0])['versions'])

    async def test_failed_public_source_is_replaced_and_collection_refills_reserve(self):
        inventory = self.runner.inventory
        seed(inventory, 1001)
        inventory.publish()
        published = read_json(inventory.base_dir / 'main/publication.json')
        failed = published['sources'][0]
        version = inventory.store.get(failed['source_id'])['versions'][failed['revision']]
        failed_url = version['source']['bookSourceUrl']
        version['validation']['next_deep_check_at'] = '2000-01-01T00:00:00+00:00'
        inventory.store.save()

        async def collect(store, client, **kwargs):
            store.ingest(source('https://replacement.example'), {'provider': 'yckceo'})
            return {'fetched_ids': 1, 'pending_count': 0}

        async def probe(item, mode='deep'):
            result = {**ready_result(inventory.policy), 'mode': mode}
            if item['bookSourceUrl'] == failed_url:
                result.update(status='invalid', kind='content_empty')
            return result

        with patch('daily_maintenance.collect_submissions', new=AsyncMock(return_value={})), \
                patch('daily_maintenance.collect_into', new=AsyncMock(return_value={'files': 0, 'errors': []})), \
                patch('daily_maintenance.YckceoHarvester.sync', side_effect=collect), \
                patch('daily_maintenance.ReadingValidator.probe', side_effect=probe):
            first = await self.runner.maintain(mode='catchup', publish=True)
            second = await self.runner.maintain(mode='catchup', collect=False, publish=True)
        self.assertEqual(first['inventory']['healthy_count'], 1000)
        self.assertEqual(first['inventory']['reserve_count'], 0)
        self.assertEqual(second['inventory']['healthy_count'], 1000)
        self.assertEqual(second['inventory']['reserve_count'], 1)
        urls = {item['bookSourceUrl'] for item in read_json(inventory.base_dir / 'full.json')}
        self.assertNotIn(failed_url, urls)
        self.assertEqual(inventory.store.manifest['count'], 1002)

    async def test_cancellation_closes_inflight_probes(self):
        refs = seed(self.runner.inventory, 1)
        started, closed = asyncio.Event(), asyncio.Event()

        async def probe(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        validator = AsyncMock()
        validator.probe.side_effect = probe
        task = asyncio.create_task(self.runner.validate(
            validator, refs, mode='deep', deadline=time.monotonic() + 10, save=False, report={}))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())

    async def test_daily_light_checks_do_not_block_deep_validation(self):
        seed(self.runner.inventory, 1)
        self.runner.inventory.store.ingest(source('https://pending.example'), {'provider': 'legacy'})
        self.runner.inventory.store.save()
        deep, light = asyncio.Event(), asyncio.Event()

        async def probe(item, mode='deep'):
            (deep if mode == 'deep' else light).set()
            await asyncio.wait_for((light if mode == 'deep' else deep).wait(), 1)
            return {**ready_result(self.runner.inventory.policy), 'mode': mode}

        with patch('daily_maintenance.ReadingValidator.probe', side_effect=probe):
            report = await self.runner.maintain(mode='daily', collect=False)
        self.assertEqual(report['deep_checked'], 1)
        self.assertEqual(report['light_checked'], 1)

    async def test_collected_exclusion_applies_before_publication(self):
        inventory = self.runner.inventory
        ref = seed(inventory, 1)[0]
        original = inventory.store.get(ref[0])['versions'][ref[1]]['source']
        inventory.store.save()
        inventory.publish()

        async def collect(store, client, **kwargs):
            store.ingest({**original, 'exploreUrl': 'R18::/adult'}, {'provider': 'yckceo'})
            return {'fetched_ids': 1, 'pending_count': 0}

        with patch('daily_maintenance.collect_submissions', new=AsyncMock(return_value={})), \
                patch('daily_maintenance.collect_into', new=AsyncMock(return_value={'files': 0, 'errors': []})), \
                patch('daily_maintenance.YckceoHarvester.sync', side_effect=collect):
            report = await self.runner.maintain(mode='catchup', publish=True)
        self.assertTrue(report['published'])
        self.assertEqual(read_json(inventory.base_dir / 'full.json'), [])
        self.assertEqual(len(inventory.store.get(ref[0])['versions']), 2)

    async def test_dry_run_keeps_raw_and_public_files_unchanged(self):
        seed(self.runner.inventory, 2)
        self.runner.inventory.store.save()
        self.runner.inventory.publish()
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        async def collect(store, client, **kwargs):
            store.ingest(source('https://new.example'), {'provider': 'yckceo'})
            return {'fetched_ids': 1, 'pending_count': 0}

        with patch('daily_maintenance.collect_submissions', new=AsyncMock(return_value={})), \
                patch('daily_maintenance.collect_into', new=AsyncMock(return_value={'files': 0, 'errors': []})), \
                patch('daily_maintenance.YckceoHarvester.sync', side_effect=collect), \
                patch('daily_maintenance.ReadingValidator.probe', new=AsyncMock(return_value=ready_result(self.runner.inventory.policy))):
            report = await self.runner.maintain(dry_run=True)
        self.assertFalse(report['published'])
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    async def test_maintenance_preserves_metadata_fields(self):
        seed(self.runner.inventory, 2)
        self.runner.inventory.store.save()
        self.runner.metadata_file.parent.mkdir(parents=True, exist_ok=True)
        self.runner.metadata_file.write_text(json.dumps({'custom': 'keep'}))
        with patch('daily_maintenance.ReadingValidator.probe', new=AsyncMock(return_value=ready_result(self.runner.inventory.policy))):
            report = await self.runner.maintain(collect=False, publish=True)
        self.assertTrue(report['published'])
        self.assertEqual(read_json(self.runner.metadata_file)['custom'], 'keep')

    async def test_shared_network_failure_does_not_poison_known_sources(self):
        refs = seed(self.runner.inventory, 20)
        self.runner.inventory.store.save()
        result = {'status': 'transient', 'kind': 'network', 'mode': 'deep', 'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION}
        validator = AsyncMock()
        validator.probe.return_value = result
        report = {}
        await self.runner.validate(validator, refs, mode='deep', deadline=time.monotonic() + 10, save=True, report=report)
        self.assertTrue(report['network_incident'])
        loaded = SourceStore(self.runner.inventory.store.root)
        for key, revision in refs:
            self.assertEqual(loaded.get(key)['versions'][revision]['validation']['status'], 'valid')

    async def test_stale_successes_are_not_network_health_controls(self):
        refs = seed(self.runner.inventory, 20, datetime.now(timezone.utc) - timedelta(days=30))
        result = {**ready_result(self.runner.inventory.policy), 'status': 'transient', 'kind': 'network'}
        validator = AsyncMock()
        validator.probe.return_value = result
        report = {}
        await self.runner.validate(validator, refs, mode='deep', deadline=time.monotonic() + 10,
                                   save=True, report=report)
        self.assertFalse(report.get('network_incident', False))
        self.assertEqual(report['deep_checked'], 20)

    async def test_checkpoint_restores_results_and_checks_input_identity(self):
        path = self.root / 'checkpoints'
        validator = BatchValidator(batch_size=1, checkpoint_dir=path)
        values = [source(), source('https://two.example')]
        validator.input_hash = __import__('source_store').digest(values)
        validator.save_checkpoint(1, [values[0]], [], [{'valid': 1}])
        with patch.object(validator, 'validate_batch', new=AsyncMock(return_value=([values[1]], [], {'valid': 1}))) as check:
            valid, invalid, _ = await validator.validate_all(values, resume=True)
        self.assertEqual(valid, values)
        check.assert_awaited_once()
        with self.assertRaises(ValueError):
            await validator.validate_all([source('https://different.example')], resume=True)

    async def test_network_incident_preserves_confirmed_reading_failures(self):
        refs = seed(self.runner.inventory, 20)
        network = {'status': 'transient', 'kind': 'network', 'mode': 'deep',
                   'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION}
        invalid = {**network, 'status': 'invalid', 'kind': 'content_empty'}
        validator = AsyncMock()
        validator.probe.side_effect = [invalid] + [network] * 19
        report = {}
        await self.runner.validate(validator, refs, mode='deep', deadline=time.monotonic() + 10,
                                   save=True, report=report)
        self.assertTrue(report['network_incident'])
        loaded = SourceStore(self.runner.inventory.store.root)
        key, revision = refs[0]
        self.assertEqual(loaded.get(key)['versions'][revision]['validation']['status'], 'invalid')
        key, revision = refs[1]
        self.assertEqual(loaded.get(key)['versions'][revision]['validation']['status'], 'valid')


if __name__ == '__main__':
    unittest.main()
