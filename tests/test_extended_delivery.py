import json
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
from source_health import apply_result
from source_inventory import SourceInventory
from source_store import read_json, utcnow
from test_inventory_v2 import ready_result, seed
from test_maintenance_v2 import source


def attempt(status, kind, mode='deep'):
    return {'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION, 'mode': mode,
            'status': status, 'kind': kind}


def relax_publication(root, extended_min=3):
    path = root / 'config/supplement_config.json'
    config = json.loads(path.read_text())
    config['publication'] = {**config.get('publication', {}), 'extended_min': extended_min}
    path.write_text(json.dumps(config))


class ExtendedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        relax_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, url, **fields):
        return self.inventory.store.ingest({**source(url), **fields}, {'provider': 'fixture'})

    def test_tiers_are_ordered_and_labelled(self):
        seed(self.inventory, 2)
        grace = self.ingest('https://grace.example')
        self.inventory.record_result(*grace, ready_result(self.inventory.policy))
        self.inventory.record_result(*grace, attempt('transient', 'network'))
        self.inventory.record_result(*self.ingest('https://unverified.example'),
                                     attempt('unverified', 'search_empty'))
        self.ingest('https://never.example')

        export, _, report = self.inventory.select()
        self.assertEqual(report['verified_count'], 2)
        self.assertEqual(report['tiers']['verified'], 2)
        self.assertEqual(report['tiers']['unverified'], 1)
        self.assertEqual(report['tiers']['never_checked'], 1)
        self.assertEqual(report['extended_count'], report['verified_count'] + 3)

        extended = self.inventory.extended_versions(export)
        self.assertEqual([item['_source_id'] for item in extended[:len(export)]],
                         [item['_source_id'] for item in export])
        self.assertEqual([item['_tier'] for item in extended[len(export):]],
                         ['grace', 'unverified', 'never_checked'])

    def test_stale_success_lands_in_its_own_tier(self):
        seed(self.inventory, 1, datetime.now(timezone.utc) - timedelta(days=20))
        _, _, report = self.inventory.select()
        self.assertEqual(report['tiers'], {'stale_verified': 1})
        self.assertEqual(report['verified_count'], 0)

    def test_excluded_sources_never_enter_a_package(self):
        seed(self.inventory, 1)
        self.ingest('https://disabled.example', enabled=False)
        self.ingest('https://media.example', bookSourceType=1)
        self.ingest('https://browser.example', webView=True)
        self.inventory.record_result(*self.ingest('https://locked.example'),
                                     attempt('invalid', 'content_locked'))
        self.inventory.record_result(*self.ingest('https://adult.example'), attempt('blocked', 'content_audit'))
        blocked = self.inventory.store.ingest({**source('https://r18.example'), 'exploreUrl': 'R18::/adult'},
                                             {'provider': 'fixture'})
        self.inventory.audit_all()

        extended = self.inventory.extended_versions(self.inventory.select()[0])
        urls = {item['bookSourceUrl'] for item in extended}
        self.assertEqual(len(extended), 1, blocked)
        self.assertNotIn('https://disabled.example', urls)
        self.assertNotIn('https://media.example', urls)
        self.assertNotIn('https://browser.example', urls)
        self.assertNotIn('https://locked.example', urls)
        self.assertNotIn('https://r18.example', urls)

    def test_duplicate_identity_and_domain_spread_are_bounded(self):
        seed(self.inventory, 1)
        self.ingest('https://dup.example')
        self.ingest('https://dup.example#fragment')
        same_rules = [self.ingest(f'https://alias{index}.example') for index in range(2)]
        crowded = [self.ingest(f'https://crowded.example/{index}') for index in range(5)]

        extended = self.inventory.extended_versions(self.inventory.select()[0])
        urls = [item['bookSourceUrl'] for item in extended]
        self.assertEqual(len({url.split('#')[0] for url in urls}), len(urls))
        crowded_urls = [url for url in urls if url.startswith('https://crowded.example')]
        self.assertEqual(len(crowded_urls), 3)
        self.assertTrue(same_rules and crowded)

    def test_published_packages_share_the_verified_prefix(self):
        seed(self.inventory, 4)
        self.ingest('https://tail.example')
        self.inventory.publish()
        base = self.inventory.base_dir
        verified = json.loads((base / 'full.json').read_text())
        extended = json.loads((base / 'full-extended.json').read_text())
        self.assertEqual(extended[:len(verified)], verified)
        self.assertEqual((base / 'full.json').read_bytes(), (base / 'main/full.json').read_bytes())
        self.assertEqual((base / 'full-extended.json').read_bytes(),
                         (base / 'main/full-extended.json').read_bytes())
        manifest = read_json(base / 'main/publication.json')
        self.assertEqual(manifest['counts']['verified'], len(verified))
        self.assertEqual(manifest['counts']['extended'], len(extended))
        self.assertEqual(manifest['counts']['unverified'], len(extended) - len(verified))
        self.assertEqual(len(read_json(base / 'manifest.json')['extended_only']), len(extended) - len(verified))

    def test_shortage_reports_the_real_count(self):
        seed(self.inventory, 2)
        _, _, report = self.inventory.select()
        self.assertEqual(report['extended_count'], 2)
        self.assertTrue(report['needs_replenishment'])

    def test_package_stops_at_the_target_not_the_ceiling(self):
        self.inventory.extended_target = 3
        self.inventory.extended_max = 10
        seed(self.inventory, 2)
        for index in range(4):
            self.ingest(f'https://tail{index}.example')
        _, _, report = self.inventory.select()
        self.assertEqual(report['extended_count'], 3)
        self.assertEqual(report['extended_only_count'], 1)

    def test_blocked_source_leaves_both_published_files(self):
        refs = seed(self.inventory, 3)
        self.inventory.publish()
        source_payload = self.inventory.store.get(refs[0][0])['versions'][refs[0][1]]['source']
        self.inventory.store.ingest({**source_payload, 'exploreUrl': 'R18::/adult'}, {'provider': 'fixture'})
        self.inventory.audit_all()
        self.inventory.publish()
        base = self.inventory.base_dir
        verified = json.loads((base / 'full.json').read_text())
        extended = json.loads((base / 'full-extended.json').read_text())
        self.assertEqual(len(verified), 2)
        self.assertNotIn('https://site0.example', {item['bookSourceUrl'] for item in extended})
        self.assertEqual(extended[:len(verified)], verified)

    def test_unverified_sources_are_not_published_as_verified(self):
        seed(self.inventory, 1)
        self.ingest('https://unverified.example')
        self.inventory.publish()
        payload = json.loads((self.inventory.base_dir / 'full.json').read_text())
        self.assertEqual(len(payload), 1)
        self.assertFalse(any(key.startswith('_') for item in payload for key in item))


if __name__ == '__main__':
    unittest.main()
