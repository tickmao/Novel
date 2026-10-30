import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from source_inventory import SourceInventory


def decide(needs_replenishment, pending):
    return SourceInventory.collection_decision(None, {'pending_revisions': pending},
                                               {'needs_replenishment': needs_replenishment})


class CollectionDecouplingTests(unittest.TestCase):
    def test_a_pending_backlog_no_longer_blocks_harvesting(self):
        result = decide(True, 9630)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['reason'], 'raw_inventory_pending')

    def test_an_exhausted_backlog_reports_the_shortage(self):
        result = decide(True, 0)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['reason'], 'qualified_inventory_shortage')

    def test_a_full_inventory_still_skips_collection(self):
        result = decide(False, 0)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['reason'], 'inventory_sufficient')

    def test_a_full_inventory_skips_collection_even_with_a_backlog(self):
        self.assertFalse(decide(False, 500)['allowed'])


if __name__ == '__main__':
    unittest.main()
