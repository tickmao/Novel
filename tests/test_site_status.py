import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from source_inventory import SourceInventory
from source_store import read_json
from test_inventory_v2 import seed
from test_publication_tiers import README_TEMPLATE


class SiteOutputTests(unittest.TestCase):
    """Run the publisher against the real page so its structure is covered."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        shutil.copytree(ROOT / 'docs', self.root / 'docs')
        enable_test_publication(self.root)
        (self.root / 'README.md').write_text(README_TEMPLATE)
        self.inventory = SourceInventory(self.root)
        seed(self.inventory, 3)
        self.inventory.store.save()
        self.inventory.publish()
        self.index = (self.root / 'docs' / 'index.html').read_text()
        self.manifest = read_json(self.root / 'sources/legado/main/publication.json')

    def tearDown(self):
        self.temp.cleanup()

    def test_the_page_carries_no_release_status_block(self):
        for marker in ('publication-status', 'status-bar', 'status-dot', 'status-text'):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.index)

    def test_the_page_still_reports_the_delivered_count(self):
        extended = self.manifest['counts']['extended']

        self.assertIn(f'{extended} 个纯小说书源', self.index)
        self.assertIn(f'{extended} 个书源', self.index)

    def test_nothing_is_inserted_between_the_page_and_the_footer(self):
        self.assertRegex(self.index, r'</main>\s*<footer>')

    def test_the_style_sheet_keeps_no_dead_status_rules(self):
        style = (self.root / 'docs' / 'style.css').read_text()

        self.assertNotIn('.status-bar', style)

    def test_the_readme_and_stats_keep_the_release_details(self):
        readme = (self.root / 'README.md').read_text()
        stats = (self.root / 'STATS.md').read_text()

        self.assertIn('<!-- publication-status -->', readme)
        self.assertIn('严格验证', readme)
        self.assertIn('严格验证书源：', stats)


if __name__ == '__main__':
    unittest.main()
