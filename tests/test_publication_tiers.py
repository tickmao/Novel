import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from check_publication import _counts, check
from publication_fixture import enable_test_publication
from source_inventory import SourceInventory
from source_store import read_json
from test_inventory_v2 import seed
from test_maintenance_v2 import source

README_TEMPLATE = (
    '# Novel\n\n'
    '| 平台 | 书源数量 |\n'
    '|------|---------|\n'
    '| Android / iOS · [阅读 (Legado)](https://gedoor.github.io/) | 0 |\n\n'
    '## 导入\n'
)
INDEX_TEMPLATE = (
    '<html><body><main>\n'
    '<span class="stat-value">0 个书源</span>\n'
    '<span class="source-count">0 个纯小说书源</span>\n'
    '聚合 0 纯小说书源\n'
    '</main></body></html>\n'
)


class TieredPublicationTests(unittest.TestCase):
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

    def publish(self):
        seed(self.inventory, 5)
        self.inventory.store.ingest({**source('https://tail.example'), 'searchUrl': '/find?q={{key}}'},
                                    {'provider': 'fixture'})
        self.inventory.store.save()
        self.inventory.publish()
        return self.inventory.base_dir

    def test_both_packages_are_consistent_and_signed(self):
        base = self.publish()
        manifest = check(self.root)
        verified = (base / 'full.json').read_bytes()
        extended = (base / 'full-extended.json').read_bytes()
        self.assertEqual(manifest['sha256'], __import__('hashlib').sha256(verified).hexdigest())
        self.assertEqual(manifest['sha256_extended'], __import__('hashlib').sha256(extended).hexdigest())
        tiers = read_json(base / 'manifest.json')
        self.assertEqual(len(tiers['verified']), manifest['counts']['verified'])
        self.assertEqual(len(tiers['extended_only']), manifest['counts']['extended_only'])
        self.assertEqual(tiers['release_id'], manifest['release_id'])

    def test_prefix_break_is_rejected(self):
        base = self.publish()
        extended = json.loads((base / 'full-extended.json').read_text())
        extended[0], extended[-1] = extended[-1], extended[0]
        (base / 'full-extended.json').write_text(json.dumps(extended, ensure_ascii=False, indent=2) + '\n')
        (base / 'main/full-extended.json').write_text(
            (base / 'full-extended.json').read_text())
        with self.assertRaises(ValueError):
            check(self.root)

    def test_visible_counts_cover_both_packages(self):
        base = self.publish()
        manifest = read_json(base / 'main/publication.json')
        readme = (self.root / 'README.md').read_text()
        index = (self.root / 'docs/index.html').read_text()
        self.assertIn(f'| {manifest["counts"]["extended"]} |', readme)
        self.assertIn('[阅读 (Legado)]', readme)
        self.assertIn(f'{manifest["counts"]["extended"]} 个纯小说书源', index)
        self.assertIn('扩展交付书源', (self.root / 'STATS.md').read_text())

    def test_current_health_only_covers_the_verified_tier(self):
        self.publish()
        self.assertEqual(check(self.root, current_health=True)['counts']['extended'], 6)

    def test_legacy_manifests_keep_working(self):
        self.assertEqual(_counts({'schema_version': 3, 'count': 7}),
                         {'verified': 7, 'legacy': 0, 'extended': 7, 'unverified': 0})

    def test_missing_release_reports_bootstrap(self):
        result = check(self.root, allow_bootstrap=True)
        self.assertEqual(result['status'], 'bootstrap')


if __name__ == '__main__':
    unittest.main()
