import json
import shutil
import sys
import tempfile
import time
import unittest
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

    async def test_dry_run_keeps_raw_and_public_files_unchanged(self):
        seed(self.runner.inventory, 2)
        self.runner.inventory.store.save()
        self.runner.inventory.publish()
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with patch('daily_maintenance.ReadingValidator.probe', new=AsyncMock(return_value=ready_result(self.runner.inventory.policy))):
            report = await self.runner.maintain(dry_run=True, collect=False)
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
