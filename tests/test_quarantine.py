import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from source_health import QUARANTINE_AFTER
from source_inventory import SourceInventory
from test_inventory_v2 import ready_result
from test_maintenance_v2 import source

STRUCTURAL = ('rule_error', 'search_empty', 'content_empty')


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, url, attempts):
        ref = self.inventory.store.ingest(source(url), {'provider': 'fixture'})
        version = self.inventory.store.get(ref[0])['versions'][ref[1]]
        version['attempts'] = [{'kind': kind} for kind in attempts]
        self.inventory.store.touch(ref[0])
        return ref

    def queued(self):
        self.inventory.store.save()
        return {ref for ref in self.inventory.queue(limit=500)}

    def test_a_corpse_stops_consuming_the_batch(self):
        dead = self.ingest('https://dead.example', STRUCTURAL * QUARANTINE_AFTER)
        fresh = self.ingest('https://fresh.example', [])

        queued = self.queued()

        self.assertNotIn(dead, queued)
        self.assertIn(fresh, queued)

    def test_one_short_of_the_threshold_is_still_retried(self):
        ref = self.ingest('https://nearly.example', list(STRUCTURAL[:QUARANTINE_AFTER - 1]))

        self.assertIn(ref, self.queued())

    def test_transport_failures_are_never_quarantined(self):
        ref = self.ingest('https://flaky.example', ['timeout', 'network', 'server_error', 'tls_error'])

        self.assertIn(ref, self.queued())

    def test_a_new_revision_gets_a_fresh_chance(self):
        key, revision = self.ingest('https://revived.example', STRUCTURAL * QUARANTINE_AFTER)
        self.inventory.store.ingest({**source('https://revived.example'), 'searchUrl': '/find?q={{key}}'},
                                    {'provider': 'fixture'})

        queued = self.queued()

        self.assertNotIn((key, revision), queued)
        self.assertIn(key, {ref[0] for ref in queued})

    def test_a_published_source_is_still_checked(self):
        """A source we deliver keeps its weekly check even after old rule failures."""
        ref = self.ingest('https://published.example', STRUCTURAL * QUARANTINE_AFTER)
        self.inventory.record_result(*ref, ready_result(self.inventory.policy))
        self.inventory.store.save()
        self.inventory.publish()
        self.inventory.store.save()
        # Its turn comes round again once the day it was checked has passed.
        health = self.inventory.store.get(ref[0])['versions'][ref[1]]['validation']
        due = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        health.update(next_check_at=due, next_deep_check_at=due)
        self.inventory.store.touch(ref[0])
        self.inventory.store.save()
        self.assertIn(ref, self.inventory.queue(limit=500))


if __name__ == '__main__':
    unittest.main()
