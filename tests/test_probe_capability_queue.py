import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from source_inventory import SourceInventory
from test_maintenance_v2 import source


class ProbeCapabilityQueueTests(unittest.TestCase):
    """An explore-only source ships, but no probe can ever admit it.

    ``runtime_validator.probe`` refuses a source without ``searchUrl`` before it
    starts an engine, so a queue slot spent on one returns ``no_search_rule`` and
    nothing else. The source stays deliverable in the extended tier: only the
    probe queue and the backlog count change.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def seed(self):
        blind = {**source('https://blind.example'), 'searchUrl': ''}
        blind['exploreUrl'] = 'https://blind.example/list'
        self.blind = self.inventory.store.ingest(blind, {'provider': 'fixture'})
        self.searchable = self.inventory.store.ingest(source('https://search.example'),
                                                      {'provider': 'fixture'})
        self.inventory.store.save()

    def test_an_unsearchable_source_never_takes_a_probe_slot(self):
        self.seed()

        queued = self.inventory.queue(limit=50)

        self.assertIn(self.searchable, queued)
        self.assertNotIn(self.blind, queued)

    def test_an_unsearchable_source_still_ships_in_the_extended_tier(self):
        self.seed()

        delivered = [item['bookSourceUrl'] for item in self.inventory.extended_versions([])]

        self.assertIn('https://blind.example', delivered)
        self.assertIn('https://search.example', delivered)

    def test_the_backlog_separates_unreachable_revisions_from_pending_work(self):
        self.seed()

        progress = self.inventory.revalidation_progress()

        self.assertEqual(progress['pending_revisions'], 1)
        self.assertEqual(progress['unsearchable_revisions'], 1)


if __name__ == '__main__':
    unittest.main()
