import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from source_health import (ACCESS_TERMINAL_AFTER, QUARANTINE_AFTER, REPEATED_TERMINAL_AFTER,
                           TLS_TERMINAL_AFTER, terminal)
from source_inventory import SourceInventory
from test_inventory_v2 import ready_result
from test_maintenance_v2 import source

STRUCTURAL = ('rule_error', 'search_empty', 'content_empty')


class TerminalStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, url, attempts, failures=None):
        ref = self.inventory.store.ingest(source(url), {'provider': 'fixture'})
        version = self.inventory.store.get(ref[0])['versions'][ref[1]]
        version['attempts'] = [{'kind': kind} for kind in attempts]
        if failures is not None:
            version['validation']['consecutive_failures'] = failures
        self.inventory.store.touch(ref[0])
        return ref

    def queued(self):
        self.inventory.store.save()
        return {ref for ref in self.inventory.queue(limit=500)}

    def test_a_broken_certificate_parks_after_two_attempts(self):
        ref = self.ingest('https://tls.example', ['tls_error'] * TLS_TERMINAL_AFTER)

        self.assertNotIn(ref, self.queued())
        version = self.inventory.store.get(ref[0])['versions'][ref[1]]
        self.assertEqual(version['validation']['terminal']['kind'], 'tls')

    def test_one_tls_failure_is_still_retried(self):
        ref = self.ingest('https://tls-one.example', ['tls_error'])

        self.assertIn(ref, self.queued())

    def test_an_access_gate_parks_after_three_attempts(self):
        ref = self.ingest('https://gate.example', ['http_forbidden'] * ACCESS_TERMINAL_AFTER)

        self.assertNotIn(ref, self.queued())

    def test_repeated_failure_parks_without_a_ruling_on_the_site(self):
        ref = self.ingest('https://never.example', ['timeout'], failures=REPEATED_TERMINAL_AFTER)

        self.assertNotIn(ref, self.queued())
        version = self.inventory.store.get(ref[0])['versions'][ref[1]]
        self.assertEqual(version['validation']['terminal']['kind'], 'repeated')

    def test_structural_failures_keep_their_existing_quarantine(self):
        ref = self.ingest('https://dead.example', list(STRUCTURAL * QUARANTINE_AFTER))

        self.assertNotIn(ref, self.queued())

    def test_a_parked_revision_returns_when_its_date_passes(self):
        ref = self.ingest('https://tls.example', ['tls_error'] * TLS_TERMINAL_AFTER)
        self.inventory.store.save()
        self.assertNotIn(ref, self.inventory.queue(limit=500))
        later = datetime.now(timezone.utc) + timedelta(days=31)

        self.assertIn(ref, self.inventory.queue(limit=500, now=later))

    def test_a_new_revision_is_not_blocked_by_an_old_parking(self):
        self.ingest('https://revived.example', ['tls_error'] * TLS_TERMINAL_AFTER)
        self.inventory.store.save()
        ref = self.inventory.store.ingest({**source('https://revived.example'),
                                           'searchUrl': '/find?q={{key}}'}, {'provider': 'fixture'})

        self.assertIn(ref, self.inventory.queue(limit=500))

    def test_a_success_clears_a_parked_revision(self):
        ref = self.ingest('https://recovered.example', ['tls_error'] * TLS_TERMINAL_AFTER)
        self.inventory.store.save()
        self.assertNotIn(ref, self.inventory.queue(limit=500))
        self.inventory.record_result(*ref, ready_result(self.inventory.policy))
        version = self.inventory.store.get(ref[0])['versions'][ref[1]]

        self.assertIsNone(terminal(version))
        self.assertNotIn('terminal', version['validation'])

    def test_a_published_source_keeps_its_check(self):
        ref = self.ingest('https://published.example', ['tls_error'] * TLS_TERMINAL_AFTER)
        self.inventory.record_result(*ref, ready_result(self.inventory.policy))
        self.inventory.store.save()
        self.inventory.publish()
        self.inventory.store.save()
        health = self.inventory.store.get(ref[0])['versions'][ref[1]]['validation']
        due = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        health.update(next_check_at=due, next_deep_check_at=due)
        self.inventory.store.touch(ref[0])
        self.inventory.store.save()

        self.assertIn(ref, self.inventory.queue(limit=500))

    def test_parked_revisions_leave_the_pending_backlog(self):
        self.ingest('https://tls.example', ['tls_error'] * TLS_TERMINAL_AFTER)
        fresh = self.inventory.store.ingest(source('https://fresh.example'), {'provider': 'fixture'})
        self.inventory.audit_all()
        self.inventory.queue(limit=500)
        progress = self.inventory.revalidation_progress()

        self.assertEqual(progress['parked_revisions'], 1)
        self.assertEqual(progress['pending_revisions'], 1)
        self.assertIn(fresh[0], {ref[0] for ref in self.inventory.queue(limit=500)})


if __name__ == '__main__':
    unittest.main()
