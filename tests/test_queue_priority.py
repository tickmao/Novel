import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from source_inventory import SourceInventory
from test_inventory_v2 import ready_result
from test_maintenance_v2 import source


class QueuePriorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def fill_a_site(self):
        for suffix in ('one', 'two'):
            ref = self.inventory.store.ingest(source(f'https://cap.example/{suffix}'),
                                              {'provider': 'fixture'})
            self.inventory.record_result(*ref, ready_result(self.inventory.policy))

    def test_a_saturated_site_waits_behind_an_uncovered_one(self):
        """A third success on a full site could never be published."""
        self.fill_a_site()
        saturated = self.inventory.store.ingest(source('https://cap.example/three'), {'provider': 'fixture'})
        uncovered = self.inventory.store.ingest(source('https://open.example/one'), {'provider': 'fixture'})
        self.inventory.store.save()

        queued = self.inventory.queue(limit=50)

        self.assertIn(saturated, queued)
        self.assertLess(queued.index(uncovered), queued.index(saturated))


if __name__ == '__main__':
    unittest.main()
