import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from source_inventory import SourceInventory
from test_inventory_v2 import ready_result
from test_maintenance_v2 import source


class QueueAllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def publish_due_sources(self, count):
        """Publish sources and make their next check due, as a later run sees them."""
        refs = []
        for index in range(count):
            ref = self.inventory.store.ingest(source(f'https://known{index}.example'),
                                             {'provider': 'fixture'})
            self.inventory.record_result(*ref, ready_result(self.inventory.policy))
            refs.append(ref)
        self.inventory.store.save()
        self.inventory.publish()
        due = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        for key, revision in refs:
            health = self.inventory.store.get(key)['versions'][revision]['validation']
            health.update(next_check_at=due, next_deep_check_at=due)
            self.inventory.store.touch(key)
        self.inventory.store.save()
        return set(refs)

    def fresh_sources(self, count):
        return {self.inventory.store.ingest(source(f'https://fresh{index}.example'), {'provider': 'fixture'})
                for index in range(count)}

    def test_a_short_run_still_reaches_new_candidates(self):
        """Filling the published bucket first spent the whole window on re-checks."""
        published = self.publish_due_sources(40)
        fresh = self.fresh_sources(40)

        queued = self.inventory.queue(limit=40)
        head = queued[:20]

        self.assertEqual(len(queued), 40 + 40)
        self.assertGreaterEqual(sum(1 for ref in head if ref in fresh), 12)
        self.assertTrue(set(head) & published)

    def test_the_ratio_controls_the_mix(self):
        self.publish_due_sources(20)
        self.fresh_sources(20)
        self.inventory.queue_ratio = {'published': 1, 'reserve': 1, 'new': 0}

        queued = self.inventory.queue(limit=20)
        sites = {self.inventory.store.get(ref[0])['versions'][ref[1]]['source']['bookSourceUrl']
                 for ref in queued}

        self.assertTrue(queued)
        self.assertFalse(any(url.startswith('https://fresh') for url in sites))

    def test_a_source_kept_by_more_collections_is_probed_first(self):
        """Curation is the only independent quality signal the pool has."""
        plain = self.inventory.store.ingest(source('https://plain.example'), {'provider': 'yckceo'})
        curated = self.inventory.store.ingest(source('https://curated.example'),
                                              {'provider': 'yckceo'})
        for collection in range(3):
            self.inventory.store.ingest(source('https://curated.example'),
                                        {'provider': 'shuyuans', 'item_id': str(collection)})
        self.inventory.store.save()

        queued = self.inventory.queue(limit=2)

        self.assertEqual([ref[0] for ref in queued],
                         [curated[0], plain[0]])

    def test_a_saturated_site_still_waits_behind_an_uncovered_one(self):
        for suffix in ('one', 'two'):
            ref = self.inventory.store.ingest(source(f'https://cap.example/{suffix}'), {'provider': 'fixture'})
            self.inventory.record_result(*ref, ready_result(self.inventory.policy))
        saturated = self.inventory.store.ingest(source('https://cap.example/three'), {'provider': 'fixture'})
        uncovered = self.inventory.store.ingest(source('https://open.example/one'), {'provider': 'fixture'})
        self.inventory.store.save()

        queued = self.inventory.queue(limit=50)

        self.assertIn(saturated, queued)
        self.assertLess(queued.index(uncovered), queued.index(saturated))


if __name__ == '__main__':
    unittest.main()
