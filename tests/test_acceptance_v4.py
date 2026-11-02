import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from maintenance_acceptance import MIN_DEEP_ATTEMPTS, MIN_SPOT_CHECKS, acceptance_status, daily_outcome


def run(**overrides):
    base = {'mode': 'daily', 'schema_version': 4, 'release_active': True, 'published': True,
            'deep_checked': MIN_DEEP_ATTEMPTS, 'spot_checked': MIN_SPOT_CHECKS,
            'spot': {'valid': MIN_SPOT_CHECKS},
            'inventory': {'verified_count': 148}, 'raw_progress': {'checked_revisions': 4462},
            'finished_at': datetime.now(timezone.utc).isoformat()}
    base.update(overrides)
    return base


class DailyOutcomeTests(unittest.TestCase):
    def test_a_complete_daily_run_passes_every_check(self):
        self.assertTrue(all(daily_outcome(run()).values()))

    def test_a_short_spot_check_does_not_pass(self):
        self.assertFalse(daily_outcome(run(spot_checked=10))['spot'])
        self.assertFalse(daily_outcome(run(spot={'valid': 47}))['spot'])

    def test_an_engine_incident_does_not_pass(self):
        self.assertFalse(daily_outcome(run(engine_incident='image mismatch'))['engine'])

    def test_a_retained_snapshot_still_counts_as_published(self):
        self.assertTrue(daily_outcome(run(
            published=False, publication_skipped='Runtime incident; existing snapshot retained'))['published'])

    def test_a_bare_skip_does_not_count_as_published(self):
        self.assertFalse(daily_outcome(run(published=False, publication_skipped='safe update rejected'))['published'])

    def test_a_run_without_work_does_not_pass(self):
        self.assertFalse(daily_outcome(run(deep_checked=MIN_DEEP_ATTEMPTS - 1))['work'])

    def test_a_release_that_is_not_active_does_not_pass(self):
        self.assertFalse(daily_outcome(run(release_active=False))['release'])
        self.assertFalse(daily_outcome(run(schema_version=2))['release'])


class AcceptanceWindowTests(unittest.TestCase):
    def days(self, count=7, **overrides):
        now = datetime.now(timezone.utc)
        return [run(finished_at=(now - timedelta(days=day)).isoformat(),
                    raw_progress={'checked_revisions': 4000 + (count - 1 - day) * 50},
                    inventory={'verified_count': 140 + (count - 1 - day)}, **overrides)
                for day in range(count - 1, -1, -1)]

    def test_seven_progressing_days_are_accepted(self):
        self.assertEqual(acceptance_status(self.days())['status'], 'accepted')

    def test_a_week_without_new_evidence_is_pending(self):
        now = datetime.now(timezone.utc)
        runs = [run(finished_at=(now - timedelta(days=day)).isoformat()) for day in range(6, -1, -1)]

        status = acceptance_status(runs)

        self.assertEqual(status['status'], 'pending')
        self.assertTrue(all(day['passed'] for day in status['days']))

    def test_a_missing_day_is_pending(self):
        now = datetime.now(timezone.utc)
        runs = [run(finished_at=(now - timedelta(days=day)).isoformat()) for day in (6, 5, 4, 3, 2, 0)]

        self.assertEqual(acceptance_status(runs)['status'], 'pending')

    def test_catchup_runs_do_not_count_as_days(self):
        self.assertEqual(acceptance_status(self.days(**{'mode': 'catchup'}))['observed_days'], 0)


if __name__ == '__main__':
    unittest.main()
