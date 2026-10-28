import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from safe_updater import SafeUpdateError
from source_inventory import SourceInventory
from source_store import read_json
from test_inventory_v2 import seed
from test_maintenance_v2 import source


def strict_publication(root):
    path = root / 'config/supplement_config.json'
    config = json.loads(path.read_text())
    config['publication'] = {'initial_healthy_min': 950, 'initial_reserve_min': 300, 'extended_min': 200}
    path.write_text(json.dumps(config))


class TieredGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        strict_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_extended_package_ships_before_the_verified_gate(self):
        seed(self.inventory, 250)
        main = self.inventory.base_dir / 'main'
        main.mkdir(parents=True, exist_ok=True)
        previous = json.loads(json.dumps([
            {key: value for key, value in item.items() if not key.startswith('_')}
            for item in self.inventory.select()[0][:10]
        ]))
        (main / 'full.json').write_text(json.dumps(previous, ensure_ascii=False, indent=2) + '\n')
        self.assertFalse(self.inventory.publication_gate()['ready'])
        self.assertTrue(self.inventory.publication_gate(extended=True)['ready'])

        report = self.inventory.publish()
        base = self.inventory.base_dir
        self.assertEqual(report['verified_count'], 250)
        self.assertTrue(self.inventory.publication_gate()['ready'])
        verified = json.loads((main / 'full.json').read_text())
        self.assertEqual(len(verified), 250)
        self.assertNotEqual(verified, previous)
        extended = json.loads((base / 'full-extended.json').read_text())
        self.assertEqual(extended[:len(verified)], verified)
        manifest = read_json(main / 'publication.json')
        self.assertEqual(manifest['count'], len(verified))
        self.assertEqual(manifest['counts']['extended'], len(extended))
        self.assertEqual(manifest['status'], 'degraded')

    def test_no_package_ships_without_engine_evidence(self):
        seed(self.inventory, 250)
        (self.root / 'reports/runtime/compatibility.json').unlink()
        self.assertFalse(self.inventory.publication_gate(extended=True)['ready'])
        with self.assertRaises(SafeUpdateError):
            self.inventory.publish()
        self.assertFalse((self.inventory.base_dir / 'full-extended.json').exists())

    def test_extended_tail_never_leaks_into_the_strict_file(self):
        seed(self.inventory, 200)
        for index in range(50):
            self.inventory.store.ingest(
                {**source(f'https://candidate{index}.example'), 'searchUrl': f'/find{index}?q={{{{key}}}}'},
                {'provider': 'fixture'})
        self.inventory.publish()
        verified = json.loads((self.inventory.base_dir / 'full.json').read_text())
        extended = json.loads((self.inventory.base_dir / 'full-extended.json').read_text())
        self.assertEqual(len(verified), 200)
        self.assertGreater(len(extended), len(verified))
        self.assertTrue(all(item['bookSourceUrl'].startswith('https://site')
                            for item in verified))


if __name__ == '__main__':
    unittest.main()
