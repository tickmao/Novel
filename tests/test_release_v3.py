import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from source_health import VALIDATOR_VERSION, apply_result, eligibility
from source_inventory import SourceInventory
from source_store import read_json, utcnow
from test_inventory_v2 import ready_result, seed
from publication_fixture import enable_test_publication
from maintenance_acceptance import acceptance_status
from runtime_config import ENGINE_COMMIT, runtime_fingerprint


class EvidenceTests(unittest.TestCase):
    def test_engine_failure_does_not_destroy_reading_evidence(self):
        policy = SourceInventory().policy
        version = {}
        success = ready_result(policy)
        apply_result(version, success)
        previous = json.loads(json.dumps(version['validation']))
        apply_result(version, {'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION,
                               'mode': 'deep', 'status': 'unverified', 'kind': 'engine_unavailable'})
        self.assertEqual(version['validation']['last_deep_success_at'], previous['last_deep_success_at'])
        self.assertEqual(version['validation']['status'], 'valid')
        self.assertEqual(version['attempts'][-1]['kind'], 'engine_unavailable')

    def test_search_success_cannot_clear_a_confirmed_reading_failure(self):
        policy = SourceInventory().policy
        version = {}
        apply_result(version, ready_result(policy))
        apply_result(version, {'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION,
                               'mode': 'deep', 'status': 'invalid', 'kind': 'content_empty'})
        result = ready_result(policy)
        result.update(mode='light', kind='search')
        apply_result(version, result)
        self.assertIsNone(eligibility(version, policy.auditor.version))

    def test_search_success_cannot_extend_transient_grace(self):
        policy = SourceInventory().policy
        now = datetime.now(timezone.utc)
        version = {}
        apply_result(version, ready_result(policy, now - timedelta(hours=74)))
        apply_result(version, {'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION,
                               'mode': 'deep', 'status': 'transient', 'kind': 'timeout'})
        result = ready_result(policy)
        result.update(mode='light', kind='search')
        apply_result(version, result)
        self.assertIsNone(eligibility(version, policy.auditor.version, now))

    def test_static_evidence_is_not_runtime_evidence(self):
        policy = SourceInventory().policy
        version = {}
        result = ready_result(policy)
        result['validator_version'] = 'static-reading-v2'
        apply_result(version, result)
        self.assertIsNone(eligibility(version, policy.auditor.version))


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_runtime_acceptance_command_fails_without_evidence(self):
        result = subprocess.run([
            sys.executable, '-B', str(ROOT / 'scripts/probe_runtime.py'), 'gate',
            '--base-dir', str(self.root), '--require-ready',
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = read_json(self.root / 'reports/runtime/compatibility.json')
        self.assertEqual(report['status'], 'blocked')

    def test_runtime_acceptance_command_accepts_matching_evidence(self):
        output = self.root / 'reports/runtime'
        output.mkdir(parents=True)
        identity = {'engine_commit': ENGINE_COMMIT, 'runtime_fingerprint': runtime_fingerprint(),
                    'validator_version': VALIDATOR_VERSION, 'container_digest': 'sha256:' + 'a' * 64}
        (output / 'fixtures.json').write_text(json.dumps({**identity, 'fixture_count': 8, 'fixture_failures': 0}))
        (output / 'sample.json').write_text(json.dumps({
            **identity, 'live_sample_count': 60,
            'rows': [{'result': {'status': 'valid', 'kind': 'reading'}} for _ in range(60)],
        }))
        result = subprocess.run([
            sys.executable, '-B', str(ROOT / 'scripts/probe_runtime.py'), 'gate',
            '--base-dir', str(self.root), '--require-ready', '--container-digest', identity['container_digest'],
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read_json(output / 'compatibility.json')['status'], 'passed')

    def test_old_experimental_release_cannot_bypass_first_release_gate(self):
        seed(self.inventory, 2)
        main = self.inventory.base_dir / 'main'
        main.mkdir(parents=True)
        (main / 'publication.json').write_text(json.dumps({'schema_version': 2, 'count': 45}))
        gate = self.inventory.publication_gate()
        self.assertFalse(gate['ready'])
        self.assertIn('healthy_count', gate['missing'])
        self.assertIn('reserve_count', gate['missing'])
        self.assertIn('engine_compatibility', gate['missing'])

    def test_shadow_export_does_not_replace_public_files(self):
        seed(self.inventory, 2)
        public = self.inventory.base_dir / 'full.json'
        public.parent.mkdir(parents=True, exist_ok=True)
        public.write_text('[{"original":true}]')
        self.inventory.write_shadow()
        self.assertEqual(public.read_text(), '[{"original":true}]')
        shadow = read_json(self.inventory.base_dir / 'main/shadow.json')
        self.assertEqual(shadow['inventory']['healthy_count'], 2)
        self.assertFalse(shadow['gate']['ready'])

    def test_first_release_needs_both_stock_and_runtime_evidence(self):
        seed(self.inventory, 1300)
        self.assertEqual(self.inventory.publication_gate()['missing'], ['engine_compatibility'])
        enable_test_publication(self.root)
        path = self.root / 'config/supplement_config.json'
        config = json.loads(path.read_text())
        config['publication'] = {'initial_healthy_min': 950, 'initial_reserve_min': 300}
        path.write_text(json.dumps(config))
        self.assertTrue(self.inventory.publication_gate()['ready'])
        report = self.inventory.publish()
        self.assertEqual((report['healthy_count'], report['reserve_count']), (1000, 300))

    def test_blocked_site_cannot_reenter_through_another_fragment(self):
        refs = seed(self.inventory, 1)
        original = self.inventory.store.get(refs[0][0])['versions'][refs[0][1]]['source']
        self.inventory.store.ingest({**original, 'bookSourceUrl': original['bookSourceUrl'] + '#adult',
                                     'exploreUrl': 'R18::/adult'}, {'provider': 'fixture'})
        self.inventory.audit_all()
        self.assertEqual(self.inventory.select()[2]['healthy_count'], 0)


class AcceptanceTests(unittest.TestCase):
    def test_seven_days_require_real_spot_checks(self):
        now = datetime.now(timezone.utc)
        runs = [{'mode': 'daily', 'schema_version': 3, 'finished_at': (now - timedelta(days=day)).isoformat(),
                 'release_active': True, 'inventory': {'healthy_count': 1000},
                 'spot_checked': 50, 'spot': {'valid': 50}} for day in range(6, -1, -1)]
        self.assertEqual(acceptance_status(runs)['status'], 'accepted')
        runs[-1]['spot_checked'] = 0
        self.assertEqual(acceptance_status(runs)['status'], 'pending')

    def test_missing_day_prevents_acceptance(self):
        now = datetime.now(timezone.utc)
        runs = [{'mode': 'daily', 'schema_version': 3, 'finished_at': (now - timedelta(days=day)).isoformat(),
                 'release_active': True, 'inventory': {'healthy_count': 1000},
                 'spot_checked': 50, 'spot': {'valid': 50}} for day in (8, 6, 5, 4, 3, 2, 0)]
        self.assertEqual(acceptance_status(runs)['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
