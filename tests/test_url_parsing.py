"""One dirty host must never abort a maintenance run.

``urlsplit`` raises ``ValueError`` for a netloc that is not NFKC-stable: a
full-width ``＃`` (U+FF03) or a mismatched bracket. Shared source packs carry
such labels in ``bookSourceUrl``, and a single one of them used to abort a whole
run from ``audit_all``. Every parse site must degrade instead of raising.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from reading_validator import ProbeFailure, public_url
from source_inventory import SourceInventory
from source_policy import SourcePolicy, source_url_problem
from source_store import rule_fingerprint, safe_urlsplit, source_id
from test_maintenance_v2 import source

DIRTY = 'https://api-bc.wtzw.com＃妍希'
CLEAN = 'https://books.example/path?q=1#frag'


class SafeUrlSplitTests(unittest.TestCase):
    def test_a_dirty_netloc_returns_none_instead_of_raising(self):
        self.assertIsNone(safe_urlsplit(DIRTY))
        self.assertIsNone(safe_urlsplit('https://[unclosed.example'))

    def test_a_clean_url_is_still_parsed(self):
        self.assertEqual(safe_urlsplit(CLEAN).hostname, 'books.example')


class RuleFingerprintTests(unittest.TestCase):
    def test_an_unparseable_url_gets_a_stable_fingerprint(self):
        item = {**source(), 'bookSourceUrl': DIRTY}
        self.assertEqual(rule_fingerprint(item), rule_fingerprint(dict(item)))

    def test_a_clean_url_keeps_the_normalised_fingerprint(self):
        self.assertEqual(
            rule_fingerprint({**source(), 'bookSourceUrl': 'HTTPS://Books.Example/x#frag'}),
            rule_fingerprint({**source(), 'bookSourceUrl': 'https://books.example/x#frag'}))


class AdmissionTests(unittest.TestCase):
    def test_a_dirty_url_is_refused_not_fatal(self):
        item = {**source(), 'bookSourceUrl': DIRTY}
        self.assertEqual(source_url_problem(item), 'invalid_url')
        self.assertEqual(SourceInventory.scope_reason(item), 'invalid_url')

    def test_the_content_audit_still_returns_a_decision(self):
        policy = SourcePolicy(ROOT)
        result = policy.audit_source({**source(), 'bookSourceUrl': DIRTY})
        self.assertIn(result['decision'], ('pass', 'review', 'block'))

    def test_the_probe_gate_reports_invalid_url(self):
        with self.assertRaises(ProbeFailure) as caught:
            public_url(DIRTY)
        self.assertEqual(caught.exception.kind, 'invalid_url')

    def test_a_public_url_still_passes_the_probe_gate(self):
        self.assertIsNone(public_url('https://books.example/'))


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_dirty_url_falls_back_to_the_source_id(self):
        item = {**source(), 'bookSourceUrl': DIRTY}
        self.assertEqual(self.inventory.site(item), source_id(item))

    def test_a_clean_url_keeps_its_site(self):
        self.assertEqual(
            self.inventory.site({**source(), 'bookSourceUrl': 'http://m.haitangwx.com/'}),
            'haitangwx.com')


if __name__ == '__main__':
    unittest.main()
