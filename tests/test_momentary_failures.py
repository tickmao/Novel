"""A failed probe only outranks the reading it was meant to confirm.

Delivery had been decided by the newest attempt instead of by the completed
reading. Every run light-probes the whole publication, so one timed-out search
could drop a source that read two chapters yesterday, and the seven-day retry
delay made that loss last a week. These tests pin who may revoke a reading.
"""

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from runtime_config import VALIDATOR_VERSION
from source_health import apply_result, eligibility
from source_inventory import SourceInventory
from test_inventory_v2 import ready_result
from test_maintenance_v2 import source


def failure(kind, status='transient', mode='deep', now=None):
    return {'status': status, 'kind': kind, 'mode': mode, 'validator_version': VALIDATOR_VERSION,
            'checked_at': (now or datetime.now(timezone.utc)).isoformat()}


class MomentaryFailureTests(unittest.TestCase):
    def setUp(self):
        self.policy = SourceInventory().policy
        self.now = datetime.now(timezone.utc)

    def read_version(self, now=None):
        version = {}
        apply_result(version, ready_result(self.policy, now or self.now))
        return version

    def eligible(self, version, now=None):
        return eligibility(version, self.policy.auditor.version, now or self.now)

    def test_a_failed_search_requests_a_reading_instead_of_revoking_one(self):
        version = self.read_version()

        apply_result(version, failure('rule_error', status='invalid', mode='light',
                                      now=self.now + timedelta(hours=1)))

        health = version['validation']
        self.assertEqual(health['status'], 'valid')
        self.assertNotIn('terminal', health)
        self.assertEqual(health['last_failure']['kind'], 'rule_error')
        self.assertEqual(health['last_failure']['mode'], 'light')
        # The verdict stands, and the source is due for the reading that can change it.
        self.assertEqual(health['next_deep_check_at'],
                         (self.now + timedelta(hours=1)).isoformat())
        self.assertEqual(self.eligible(version, self.now + timedelta(hours=2)), 'ready')

    def test_a_timed_out_reading_does_not_revoke_the_reading_it_confirms(self):
        version = self.read_version()

        for hours in (1, 2, 3, 4, 5, 6):
            apply_result(version, failure('timeout', now=self.now + timedelta(hours=hours)))

        health = version['validation']
        self.assertEqual(health['status'], 'valid')
        self.assertEqual(health['consecutive_failures'], 6)
        # Six timeouts are a whole day of weather, not a verdict: repeated failures
        # must not park a source whose chapters were read.
        self.assertNotIn('terminal', health)
        self.assertEqual(self.eligible(version, self.now + timedelta(hours=7)), 'ready')

    def test_a_blocked_page_does_not_revoke_a_reading(self):
        version = self.read_version()

        apply_result(version, failure('http_forbidden', now=self.now + timedelta(hours=1)))

        self.assertEqual(version['validation']['status'], 'valid')
        self.assertEqual(self.eligible(version, self.now + timedelta(hours=2)), 'ready')

    def test_a_finding_about_the_source_decides_even_from_a_search_probe(self):
        version = self.read_version()

        apply_result(version, failure('content_locked', status='invalid', mode='light',
                                      now=self.now + timedelta(hours=1)))

        # Keeping the verdict would also hide the kind that excludes the source
        # from the package, so a finding about the source always wins.
        self.assertEqual(version['validation']['status'], 'invalid')
        self.assertEqual(version['validation']['kind'], 'content_locked')
        self.assertIsNone(self.eligible(version, self.now + timedelta(hours=2)))

    def test_a_structural_reading_failure_still_revokes_the_reading(self):
        version = self.read_version()

        apply_result(version, failure('content_empty', status='invalid',
                                      now=self.now + timedelta(hours=1)))

        self.assertEqual(version['validation']['status'], 'invalid')
        self.assertIsNone(self.eligible(version, self.now + timedelta(hours=2)))

    def test_repeated_structural_failures_still_park_the_revision(self):
        version = self.read_version()

        for hours in (1, 2, 3):
            apply_result(version, failure('toc_empty', status='invalid',
                                          now=self.now + timedelta(hours=hours)))

        self.assertEqual(version['validation']['terminal']['kind'], 'structural')

    def test_a_source_without_a_reading_is_still_judged_by_its_failures(self):
        version = {}
        apply_result(version, failure('timeout'))

        self.assertEqual(version['validation']['status'], 'transient')
        self.assertIsNone(self.eligible(version))

    def test_the_kept_reading_still_ages_out(self):
        version = self.read_version(now=self.now - timedelta(days=6))
        apply_result(version, failure('timeout', now=self.now - timedelta(days=1)))

        self.assertEqual(self.eligible(version, self.now), 'ready')
        self.assertIsNone(self.eligible(version, self.now + timedelta(days=2)))


class QueueAfterLightFailureTests(unittest.TestCase):
    """The request a failed search makes has to reach the next run's queue."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def publish_one(self):
        ref = self.inventory.store.ingest(source('https://reads.example', '测试书站'), {'provider': 'fixture'})
        self.inventory.record_result(*ref, ready_result(self.inventory.policy))
        self.inventory.store.save()
        return ref

    def test_a_light_failure_puts_a_published_source_in_the_deep_queue(self):
        ref = self.publish_one()
        self.assertEqual(self.inventory.queue(limit=10), [])

        self.inventory.record_result(*ref, failure('timeout', mode='light'))

        self.assertEqual(self.inventory.queue(limit=10), [ref])
        self.assertEqual(len(self.inventory.select()[0]), 1)


if __name__ == '__main__':
    unittest.main()
