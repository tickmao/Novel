import asyncio
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

from daily_maintenance import DailyMaintenance
from publication_fixture import enable_test_publication
from source_store import SourceStore, utcnow
from test_inventory_v2 import ready_result, seed
from test_maintenance_v2 import source


def result(status, kind):
    from runtime_config import VALIDATOR_VERSION
    return {'status': status, 'kind': kind, 'mode': 'deep', 'checked_at': utcnow(),
            'validator_version': VALIDATOR_VERSION}


class IncidentGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.runner = DailyMaintenance(self.root)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def validate(self, refs, outcomes):
        validator = AsyncMock()
        validator.probe.side_effect = outcomes
        report = {}
        await self.runner.validate(validator, refs, mode='deep', deadline=time.monotonic() + 10,
                                   save=True, report=report)
        return report

    async def test_mass_failure_across_many_sites_is_an_incident(self):
        refs = seed(self.runner.inventory, 20)
        report = await self.validate(refs, [result('transient', 'network')] * 20)
        self.assertTrue(report['network_incident'])

    async def test_failure_on_one_site_is_not_a_network_incident(self):
        refs = [self.runner.inventory.store.ingest(source(f'https://same.example/{index}'),
                                                   {'provider': 'fixture'}) for index in range(20)]
        report = await self.validate(refs, [result('transient', 'network')] * 20)
        self.assertFalse(report.get('network_incident', False))
        self.assertEqual(report['deep_checked'], 20)

    async def test_incident_threshold_needs_sixty_percent(self):
        refs = seed(self.runner.inventory, 20)
        outcomes = [result('transient', 'network')] * 11 + [result('invalid', 'search_empty')] * 9
        report = await self.validate(refs, outcomes)
        self.assertFalse(report.get('network_incident', False))

    async def test_engine_failure_aborts_without_recording_evidence(self):
        refs = seed(self.runner.inventory, 2)
        self.runner.inventory.store.save()
        store = self.runner.inventory.store
        before = {ref: store.get(ref[0])['versions'][ref[1]]['validation']['status'] for ref in refs}
        report = await self.validate(refs, [result('unverified', 'engine_protocol')] * 2)
        self.assertTrue(report['engine_incident'])
        loaded = SourceStore(self.runner.inventory.store.root)
        for key, revision in refs:
            self.assertEqual(loaded.get(key)['versions'][revision]['validation']['status'], before[key, revision])


if __name__ == '__main__':
    unittest.main()
