import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from source_inventory import SourceInventory
from test_inventory_v2 import seed
from test_publication_tiers import README_TEMPLATE

STAMP = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC')


class SiteStatusTests(unittest.TestCase):
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

    def tearDown(self):
        self.temp.cleanup()

    def test_the_status_lives_in_its_marker_block(self):
        self.assertEqual(self.index.count('<!-- publication-status -->'), 1)
        self.assertEqual(self.index.count('<!-- /publication-status -->'), 1)
        self.assertEqual(self.index.count('id="publication-status"'), 1)
        self.assertIn('class="status-bar"', self.index)

    def test_the_status_sits_between_the_page_and_the_footer(self):
        block = self.index.index('<!-- publication-status -->')

        self.assertGreater(block, self.index.rindex('</main>'))
        self.assertLess(block, self.index.index('<footer>'))

    def test_the_old_stray_paragraph_is_gone(self):
        self.assertNotIn('<p id="publication-status">', self.index)

    def test_the_status_is_readable(self):
        self.assertRegex(self.index, STAMP)
        self.assertIn('已降级', self.index)
        self.assertNotRegex(self.index, r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')

    def test_the_status_state_drives_the_indicator(self):
        self.assertIn('data-state="degraded"', self.index)
        self.assertIn('.status-bar[data-state="healthy"]', (self.root / 'docs/style.css').read_text())

    def test_a_second_publish_does_not_duplicate_the_block(self):
        self.inventory.publish()

        again = (self.root / 'docs' / 'index.html').read_text()

        self.assertEqual(again.count('id="publication-status"'), 1)
        self.assertEqual(again.count('<!-- publication-status -->'), 1)


if __name__ == '__main__':
    unittest.main()
