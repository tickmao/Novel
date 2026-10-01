import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from source_inventory import SourceInventory
from source_health import apply_result, eligibility, VALIDATOR_VERSION
from source_store import SourceStore, utcnow
from test_maintenance_v2 import source
from publication_fixture import enable_test_publication
from runtime_config import runtime_fingerprint


def ready_result(policy, now=None):
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    return {'status': 'valid', 'kind': 'reading', 'mode': 'deep', 'checked_at': stamp,
            'sample': {'books': 2, 'chapters': 4},
            'runtime_fingerprint': runtime_fingerprint(),
            'validator_version': VALIDATOR_VERSION, 'audit': {
                'decision': 'pass', 'complete': True, 'policy_version': policy.auditor.version,
                'checked_at': stamp, 'evidence': [],
            }}


def seed(inventory, count, now=None):
    refs = []
    for index in range(count):
        item = source(f'https://site{index}.example', f'测试书站{index}')
        ref = inventory.store.ingest(item, {'provider': 'fixture'})
        inventory.record_result(*ref, ready_result(inventory.policy, now))
        refs.append(ref)
    return refs


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_publish_exact_target_and_consistent_mirrors(self):
        seed(self.inventory, 1010)
        report = self.inventory.publish()
        self.assertEqual(report['export_count'], 1000)
        self.assertEqual(report['reserve_count'], 10)
        base = self.inventory.base_dir
        self.assertEqual((base / 'full.json').read_bytes(), (base / 'main/full.json').read_bytes())
        public = json.loads((base / 'full.json').read_text())
        self.assertEqual(len({item['bookSourceUrl'] for item in public}), 1000)
        self.assertFalse(any(key.startswith('_') for item in public for key in item))

    def test_shortage_is_reported_without_unverified_padding(self):
        seed(self.inventory, 12)
        self.inventory.store.ingest(source('https://unchecked.example'), {'provider': 'fixture'})
        report = self.inventory.publish()
        self.assertEqual(report['export_count'], 12)
        self.assertEqual(report['status'], 'degraded')

    def test_adult_revision_prevents_fallback_to_old_version(self):
        refs = seed(self.inventory, 1)
        current = self.inventory.store.get(refs[0][0])['versions'][refs[0][1]]['source']
        self.inventory.store.ingest({**current, 'bookSourceComment': '成人文学专区'}, {'provider': 'fixture'})
        self.inventory.audit_all()
        self.assertEqual(self.inventory.select()[2]['export_count'], 0)

    def test_grace_and_expiration_are_bounded(self):
        now = datetime.now(timezone.utc)
        refs = seed(self.inventory, 1, now)
        record = self.inventory.store.get(refs[0][0])
        version = record['versions'][refs[0][1]]
        failure = {'status': 'transient', 'kind': 'timeout', 'mode': 'light',
                   'checked_at': (now + timedelta(hours=1)).isoformat(), 'validator_version': VALIDATOR_VERSION}
        apply_result(version, failure)
        self.assertEqual(eligibility(version, self.inventory.policy.auditor.version, now + timedelta(hours=2)), 'grace')
        self.assertIsNone(eligibility(version, self.inventory.policy.auditor.version, now + timedelta(hours=73)))
        apply_result(version, failure)
        apply_result(version, failure)
        self.assertIsNone(eligibility(version, self.inventory.policy.auditor.version, now + timedelta(hours=2)))
        apply_result(version, ready_result(self.inventory.policy, now))
        self.assertIsNone(eligibility(version, self.inventory.policy.auditor.version, now + timedelta(days=8)))

    def test_saved_results_survive_restart_and_unsupported_is_not_retried(self):
        item = source()
        key, revision = self.inventory.store.ingest(item, {'provider': 'fixture'})
        self.inventory.record_result(key, revision, {'status': 'unsupported', 'kind': 'unsupported', 'mode': 'deep',
                                                    'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION,
                                                    'runtime_fingerprint': runtime_fingerprint()})
        self.inventory.store.save()
        loaded = SourceInventory(self.root)
        self.assertEqual(loaded.queue(), [])
        item['searchUrl'] = '/fixed?q={{key}}'
        new_ref = loaded.store.ingest(item, {'provider': 'fixture'})
        self.assertIn(new_ref, loaded.queue())

    def test_seven_day_rotation_replaces_failed_sources_from_reserve(self):
        now = datetime.now(timezone.utc)
        refs = seed(self.inventory, 1050, now)
        for day in range(7):
            for ref in refs[day * 5:day * 5 + 5]:
                self.inventory.record_result(*ref, {'status': 'invalid', 'kind': 'content_empty', 'mode': 'deep',
                                                    'checked_at': (now + timedelta(days=day)).isoformat(),
                                                    'validator_version': VALIDATOR_VERSION})
            export, reserve, report = self.inventory.select(now + timedelta(days=day))
            self.assertEqual(len(export), 1000)
            self.assertEqual(len(reserve), 50 - (day + 1) * 5)

    def test_light_success_does_not_postpone_deep_validation(self):
        now = datetime.now(timezone.utc)
        ref = seed(self.inventory, 1, now)[0]
        result = ready_result(self.inventory.policy, now + timedelta(hours=23))
        result.update(mode='light', kind='search')
        result['audit']['complete'] = False
        self.inventory.record_result(*ref, result)
        self.assertIn(ref, self.inventory.queue(now=now + timedelta(hours=25)))

    def test_publication_retention_preserves_legacy_backups(self):
        backups = self.inventory.updater.backup_dir
        backups.mkdir(parents=True)
        legacy = [backups / f'full_202603{day:02d}_120000.json' for day in range(1, 13)]
        for path in legacy:
            path.write_text('[]')
        self.assertFalse(self.inventory.updater.rollback())
        seed(self.inventory, 1)
        self.inventory.publish()
        self.inventory.publish()
        for path in legacy:
            self.assertEqual(path.read_text(), '[]')
        self.assertEqual(len(self.inventory.updater.list_backups()), 1)

    def test_unchecked_candidates_are_not_starved_by_high_ranked_retries(self):
        now = datetime.now(timezone.utc)
        retry = self.inventory.store.ingest(source('https://retry.example'), {'provider': 'fixture'})
        fresh = self.inventory.store.ingest(source('https://fresh.example'), {'provider': 'fixture'})
        self.inventory.record_result(*retry, {
            'status': 'transient', 'kind': 'timeout', 'mode': 'deep',
            'checked_at': (now - timedelta(days=2)).isoformat(), 'validator_version': VALIDATOR_VERSION,
        })
        def rank(item):
            return {'selectionScore': 100 if item['bookSourceUrl'] == 'https://retry.example' else 0}
        with patch.object(self.inventory.policy, 'enrich_source', side_effect=rank):
            self.assertEqual(self.inventory.queue(limit=1, now=now), [fresh])
            self.assertEqual(self.inventory.queue(limit=2, now=now), [fresh, retry])

    def test_grace_limit_scales_with_a_small_publication(self):
        refs = seed(self.inventory, 40)
        for ref in refs[:5]:
            self.inventory.record_result(*ref, {
                'status': 'transient', 'kind': 'timeout', 'mode': 'light',
                'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION,
            })
        export, _, report = self.inventory.select()
        self.assertEqual(report['grace_count'], 1)
        self.assertLessEqual(report['grace_count'] / len(export), 0.05)


if __name__ == '__main__':
    unittest.main()
