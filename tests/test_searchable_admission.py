import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from publication_fixture import enable_test_publication
from source_inventory import SourceInventory
from test_maintenance_v2 import source


class ScopeReasonTests(unittest.TestCase):
    def test_labels_and_emoji_are_not_source_urls(self):
        for value in ('使用说明', '🐧', '飛龍在天', '夸克原网', 'DQuestQBall#001'):
            with self.subTest(value=value):
                self.assertEqual(SourceInventory.scope_reason(
                    {**source(), 'bookSourceUrl': value}), 'invalid_url')

    def test_a_bare_hostname_has_no_scheme(self):
        for value in ('www.kptxt.com', 'novel.html5.qq.com'):
            with self.subTest(value=value):
                self.assertEqual(SourceInventory.scope_reason(
                    {**source(), 'bookSourceUrl': value}), 'invalid_url')

    def test_an_empty_url_is_rejected(self):
        self.assertEqual(SourceInventory.scope_reason(
            {**source(), 'bookSourceUrl': ''}), 'invalid_url')
        self.assertEqual(SourceInventory.scope_reason(
            {**source(), 'bookSourceUrl': '   '}), 'invalid_url')

    def test_a_real_source_still_passes(self):
        self.assertIsNone(SourceInventory.scope_reason(source('https://books.example')))
        self.assertIsNone(SourceInventory.scope_reason(source('  https://books.example  ')))

    def test_disabled_and_non_novel_keep_their_own_reasons(self):
        self.assertEqual(SourceInventory.scope_reason({**source(), 'enabled': False}), 'disabled')
        self.assertEqual(SourceInventory.scope_reason({**source(), 'bookSourceType': 1}), 'non_novel')


class SearchableFillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        enable_test_publication(self.root)
        self.inventory = SourceInventory(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def seed_blind_and_searchable(self):
        for index in range(3):
            blind = {**source(f'https://blind{index}.example'), 'searchUrl': ''}
            blind['exploreUrl'] = 'https://blind.example/list'
            self.inventory.store.ingest(blind, {'provider': 'fixture'})
        for index in range(3):
            self.inventory.store.ingest(source(f'https://search{index}.example'),
                                        {'provider': 'fixture'})
        self.inventory.store.save()

    def test_searchable_candidates_fill_the_package_first(self):
        self.seed_blind_and_searchable()

        result = self.inventory.extended_versions([])

        self.assertEqual(len(result), 6)
        self.assertEqual({item['bookSourceUrl'] for item in result[:3]},
                         {f'https://search{index}.example' for index in range(3)})

    def test_explore_only_sources_stay_deliverable(self):
        self.seed_blind_and_searchable()

        result = self.inventory.extended_versions([])

        self.assertEqual({item['bookSourceUrl'] for item in result[3:]},
                         {f'https://blind{index}.example' for index in range(3)})

    def test_the_report_names_the_verified_ceiling(self):
        self.seed_blind_and_searchable()

        _, _, report = self.inventory.select()

        self.assertEqual(report['extended_count'], 6)
        self.assertEqual(report['searchable_count'], 3)
        self.assertEqual(report['searchable_share'], 0.5)

    def test_invalid_urls_never_reach_the_package(self):
        self.inventory.store.ingest({**source(), 'bookSourceUrl': '使用说明'},
                                    {'provider': 'fixture'})
        self.inventory.store.ingest(source('https://books.example'), {'provider': 'fixture'})
        self.inventory.store.save()

        result = self.inventory.extended_versions([])

        self.assertEqual([item['bookSourceUrl'] for item in result], ['https://books.example'])


if __name__ == '__main__':
    unittest.main()
