import json
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from check_publication import check
from publication_fixture import enable_test_publication
from safe_updater import within_site_limit
from source_inventory import SourceInventory
from source_store import read_json, utcnow
from test_inventory_v2 import seed
from test_publication_tiers import INDEX_TEMPLATE, README_TEMPLATE

OLDER_IDENTITY = 'legado-reading-v0:old'


class ShrinkGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        config_path = self.root / 'config/supplement_config.json'
        config = json.loads(config_path.read_text())
        config['publication'] = {**config.get('publication', {}), 'extended_min': 3}
        config_path.write_text(json.dumps(config))
        (self.root / 'README.md').write_text(README_TEMPLATE)
        (self.root / 'docs').mkdir(parents=True, exist_ok=True)
        (self.root / 'docs/index.html').write_text(INDEX_TEMPLATE)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def publish_five(self):
        refs = seed(self.inventory, 5)
        self.inventory.store.save()
        self.inventory.publish()
        self.assertEqual(len(read_json(self.inventory.base_dir / 'full.json')), 5)
        return refs

    def health(self, ref):
        return self.inventory.store.get(ref[0])['versions'][ref[1]]['validation']

    def age_the_evidence(self, refs):
        for ref in refs:
            self.health(ref)['validator_version'] = OLDER_IDENTITY
            self.inventory.store.touch(ref[0])
        self.inventory.store.save()

    def refuse_the_evidence(self, refs):
        for ref in refs:
            health = self.health(ref)
            health.update(status='invalid', kind='http_error', last_check_at=utcnow())
            health.pop('error', None)
            self.inventory.store.touch(ref[0])
        self.inventory.store.save()

    def test_an_older_identity_is_not_a_rejection(self):
        refs = self.publish_five()
        self.age_the_evidence(refs[1:])

        restarted = SourceInventory(self.root)
        restarted.publish()
        manifest = read_json(restarted.base_dir / 'main/publication.json')

        self.assertEqual(manifest['count'], 5, 'carried entries must stay published')
        self.assertEqual(manifest['counts']['verified'], 1)
        self.assertEqual(manifest['counts']['legacy'], 4)
        self.assertEqual(check(self.root)['count'], 5)

    def test_carried_entries_are_not_repeated_in_the_extended_tail(self):
        refs = self.publish_five()
        self.age_the_evidence(refs[1:])

        restarted = SourceInventory(self.root)
        restarted.publish()
        manifest = read_json(restarted.base_dir / 'main/publication.json')
        extended = read_json(restarted.base_dir / 'full-extended.json')

        self.assertEqual(manifest['counts']['extended'], len(extended))
        self.assertEqual(len({item['bookSourceUrl'] for item in extended}), len(extended))
        self.assertEqual(len(read_json(restarted.base_dir / 'manifest.json')['extended_only']),
                         manifest['counts']['extended'] - manifest['counts']['verified'])

    def test_a_current_rejection_allows_the_release_to_shrink(self):
        refs = self.publish_five()
        self.refuse_the_evidence(refs[1:])

        restarted = SourceInventory(self.root)
        restarted.publish()
        manifest = read_json(restarted.base_dir / 'main/publication.json')

        self.assertEqual(manifest['count'], 1)
        self.assertEqual(manifest['counts']['verified'], 1)
        self.assertEqual(manifest['counts']['legacy'], 0)
        self.assertEqual(check(self.root)['count'], 1)

    def test_an_explained_zero_release_keeps_the_previous_file(self):
        self.publish_five()
        restarted = SourceInventory(self.root)
        for record in restarted.store.records():
            for version in record['versions'].values():
                health = version['validation']
                health['validator_version'] = OLDER_IDENTITY
            restarted.store.touch(record['source_id'])
        restarted.store.save()

        restarted.publish(allow_empty=True)
        manifest = read_json(restarted.base_dir / 'main/publication.json')

        self.assertEqual(manifest['count'], 5)
        self.assertEqual(manifest['counts']['verified'], 0)
        self.assertEqual(manifest['counts']['legacy'], 5)
        self.assertEqual(check(self.root)['count'], 5)

    def test_an_unloadable_url_is_dropped_from_the_published_file(self):
        self.publish_five()
        restarted = SourceInventory(self.root)
        for record in restarted.store.records():
            for version in record['versions'].values():
                version['validation']['validator_version'] = OLDER_IDENTITY
            restarted.store.touch(record['source_id'])
        restarted.store.save()
        base = restarted.base_dir
        entries = json.loads((base / 'full.json').read_text())
        entries[0]['bookSourceUrl'] = '使用说明'
        payload = (json.dumps(entries, ensure_ascii=False, indent=2) + '\n').encode()
        (base / 'full.json').write_bytes(payload)
        (base / 'main/full.json').write_bytes(payload)

        restarted.publish(allow_empty=True)
        manifest = read_json(base / 'main/publication.json')
        urls = [item['bookSourceUrl'] for item in json.loads((base / 'full.json').read_text())]

        self.assertNotIn('使用说明', urls)
        self.assertEqual(manifest['count'], 4)

    def test_the_extended_package_respects_the_site_budget(self):
        refs = self.publish_five()
        self.age_the_evidence(refs[1:])
        restarted = SourceInventory(self.root)
        restarted.publish()
        extended = read_json(restarted.base_dir / 'full-extended.json')

        sites = Counter(restarted.site(item) for item in extended)
        self.assertLessEqual(max(sites.values()),
                             restarted.config['inventory'].get('max_per_domain_extended', 3))


class SiteLimitTests(unittest.TestCase):
    @staticmethod
    def item(url):
        return {'bookSourceUrl': url}

    @staticmethod
    def site_of(source):
        return source['bookSourceUrl'].split('/')[2]

    def test_carried_entries_count_against_the_site_budget(self):
        prefix = [self.item('https://a.example/1'), self.item('https://a.example/2')]
        tail = [self.item('https://a.example/3'), self.item('https://b.example/1')]

        kept = within_site_limit(prefix, tail, self.site_of, 3)

        self.assertEqual([item['bookSourceUrl'] for item in kept],
                         ['https://a.example/3', 'https://b.example/1'])

    def test_a_full_site_is_skipped(self):
        prefix = [self.item(f'https://a.example/{index}') for index in range(3)]

        self.assertEqual(within_site_limit(prefix, [self.item('https://a.example/9')], self.site_of, 3), [])

    def test_without_a_site_function_the_tail_is_untouched(self):
        tail = [self.item('https://a.example/9')]

        self.assertEqual(within_site_limit([], tail, None, 3), tail)


if __name__ == '__main__':
    unittest.main()
