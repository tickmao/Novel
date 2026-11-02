import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

from daily_maintenance import DailyMaintenance
from publication_fixture import enable_test_publication
from source_store import SourceStore, utcnow
from test_inventory_v2 import seed
from test_maintenance_v2 import source


def result(status, kind):
    from runtime_config import VALIDATOR_VERSION
    return {'status': status, 'kind': kind, 'mode': 'deep', 'checked_at': utcnow(),
            'validator_version': VALIDATOR_VERSION}


class HostIsolationTests(unittest.IsolatedAsyncioTestCase):
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

    def ingest_many(self, prefix, count):
        return [self.runner.inventory.store.ingest(source(f'https://{prefix}{index}.example'),
                                                   {'provider': 'fixture'}) for index in range(count)]

    async def test_a_mass_failure_does_not_end_the_run(self):
        dead = self.ingest_many('dead', 20)
        healthy = [self.runner.inventory.store.ingest(source(f'https://ok{index}.example'),
                                                     {'provider': 'fixture'}) for index in range(20)]
        outcomes = [result('transient', 'network')] * 20 + [result('valid', 'reading')] * 20

        report = await self.validate(dead + healthy, outcomes)

        self.assertTrue(report['network_incident'])
        self.assertEqual(report['deep_checked'], 40)
        self.assertEqual(report['deep']['valid'], 20)
        self.assertTrue(report['hosts_isolated'])

    async def test_a_host_that_already_failed_is_not_probed_again_in_the_same_run(self):
        shared = [self.runner.inventory.store.ingest(source(f'https://shared.example/{index}'),
                                                    {'provider': 'fixture'}) for index in range(40)]

        report = await self.validate(shared, [result('transient', 'network')] * 40)

        # One host is not a shared outage, so every revision is still probed.
        self.assertFalse(report.get('network_incident', False))
        self.assertEqual(report['deep_checked'], 40)

    async def test_repeated_hosts_in_a_shared_outage_are_skipped(self):
        refs = []
        # One revision per host, cycling, so the first batch already shows five sites.
        refs = [self.runner.inventory.store.ingest(source(f'https://dead{index % 5}.example/{index}'),
                                                   {'provider': 'fixture'}) for index in range(40)]

        report = await self.validate(refs, [result('transient', 'network')] * 40)

        self.assertTrue(report['network_incident'])
        self.assertEqual(report['deep_checked'], 20)
        self.assertEqual(report['skipped_isolated'], 20)

    async def test_an_engine_incident_still_stops_the_run(self):
        refs = seed(self.runner.inventory, 40)

        report = await self.validate(refs, [result('unverified', 'engine_protocol')] * 40)

        self.assertTrue(report['engine_incident'])
        self.assertEqual(report['deep_checked'], 20)
        self.assertEqual(SourceStore(self.runner.inventory.store.root)
                         .get(refs[0][0])['versions'][refs[0][1]]['validation']['status'], 'valid')
    async def test_isolated_hosts_are_skipped_by_the_next_run(self):
        refs = self.ingest_many('dead', 20)
        await self.validate(refs, [result('transient', 'network')] * 20)
        self.runner.inventory.store.save()

        relaunched = DailyMaintenance(self.root)
        queued = {ref for ref in relaunched.inventory.queue(limit=50)}

        self.assertFalse(queued & set(refs))

    async def test_isolation_expires(self):
        refs = self.ingest_many('dead', 20)
        await self.validate(refs, [result('transient', 'network')] * 20)
        self.runner.inventory.store.save()
        isolated = self.runner.inventory.isolated_hosts()
        self.assertTrue(isolated)
        self.assertTrue(set(isolated) <= {self.runner.inventory.site(
            self.runner.inventory.store.get(key)['versions'][revision]['source']) for key, revision in refs})
        until = datetime.fromisoformat(
            self.runner.inventory.store.state['hosts'][sorted(isolated)[0]]['isolated_until'])

        self.assertFalse(self.runner.inventory.isolated_hosts(now=until + timedelta(minutes=1)))
    async def test_a_confirmed_source_keeps_its_evidence_and_gains_a_cool_down(self):
        refs = seed(self.runner.inventory, 20)
        self.runner.inventory.store.save()
        report = await self.validate(refs, [result('transient', 'network')] * 20)

        self.assertTrue(report['network_incident'])
        loaded = SourceStore(self.runner.inventory.store.root)
        for key, revision in refs:
            health = loaded.get(key)['versions'][revision]['validation']
            self.assertEqual(health['status'], 'valid')
            # The evidence survived, but the cool-down was not rolled back with it.
            self.assertEqual(health['consecutive_failures'], 1)
            self.assertIn('next_check_at', health)
            self.assertEqual(len(loaded.get(key)['versions'][revision]['attempts']), 2)

    async def test_a_failure_without_prior_evidence_is_recorded(self):
        refs = self.ingest_many('fresh', 20)
        await self.validate(refs, [result('transient', 'timeout')] * 20)

        loaded = SourceStore(self.runner.inventory.store.root)
        health = loaded.get(refs[0][0])['versions'][refs[0][1]]['validation']
        self.assertEqual(health['status'], 'transient')
        self.assertEqual(health['consecutive_failures'], 1)


if __name__ == '__main__':
    unittest.main()
