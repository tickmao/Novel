import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from maintenance_plan import plan

CONFIG = {'extended_target': 1000, 'verified_target': 950, 'reserve_target': 500,
          'reserve_min': 300, 'catchup_batch': 1500, 'daily_batch': 300}


class MaintenancePlanTests(unittest.TestCase):
    def test_gap_switches_to_catchup_and_waits_for_the_raw_backlog(self):
        decision = plan(verified_count=45, extended_count=45, reserve_count=7,
                        pending_revisions=900, config=CONFIG)
        self.assertEqual(decision['mode'], 'catchup')
        self.assertEqual(decision['batch_size'], 1500)
        self.assertEqual(decision['reason'], 'raw_inventory_pending')
        self.assertFalse(decision['collect'])
        self.assertEqual(decision['extended_gap'], 955)

    def test_exhausted_backlog_allows_collection(self):
        decision = plan(verified_count=45, extended_count=45, reserve_count=7,
                        pending_revisions=0, config=CONFIG)
        self.assertTrue(decision['collect'])
        self.assertEqual(decision['reason'], 'qualified_inventory_shortage')

    def test_complete_inventory_goes_daily_without_collection(self):
        decision = plan(verified_count=980, extended_count=1000, reserve_count=510,
                        pending_revisions=0, config=CONFIG)
        self.assertEqual(decision['mode'], 'daily')
        self.assertEqual(decision['batch_size'], 300)
        self.assertEqual(decision['reason'], 'inventory_sufficient')
        self.assertFalse(decision['collect'])
        self.assertTrue(decision['publish_full'])

    def test_verified_gap_alone_keeps_the_loop_running(self):
        decision = plan(verified_count=40, extended_count=1000, reserve_count=500,
                        pending_revisions=10, config=CONFIG)
        self.assertEqual(decision['mode'], 'catchup')
        self.assertFalse(decision['publish_full'])
        self.assertTrue(decision['publish_extended'])

    def test_decision_is_deterministic(self):
        first = plan(verified_count=1, extended_count=1, reserve_count=1, pending_revisions=1, config=CONFIG)
        second = plan(verified_count=1, extended_count=1, reserve_count=1, pending_revisions=1, config=CONFIG)
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
