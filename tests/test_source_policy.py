import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from source_policy import SourcePolicy
from test_maintenance_v2 import source


class SourcePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = SourcePolicy(ROOT)

    def test_common_names_keep_canonical_spelling(self):
        for name, url, expected in [
            ('QQ阅读', 'https://book.qq.com', '腾讯阅读'),
            ('56书库', 'https://www.56shuku.example', '五六书库'),
            ('SF轻小说', 'https://book.sfacg.com', '菠萝包轻小说'),
        ]:
            with self.subTest(name=name):
                self.assertEqual(self.policy.canonicalize_name(name, url)[0], expected)

    def test_external_numeric_fields_do_not_crash_ranking(self):
        item = {**source(), 'lastUpdateTime': '2026-01-01', 'respondTime': None, 'weight': 'unknown'}
        self.assertIsInstance(self.policy.enrich_source(item)['selectionScore'], float)

    def test_genres_are_not_a_reason_to_block(self):
        for name in ['BL文库', '纯爱小说', '百合文学', '腐文阁']:
            with self.subTest(name=name):
                self.assertEqual(self.policy.audit_source(source(name=name))['decision'], 'pass')

    def test_adult_brands_and_sections_are_blocked(self):
        for item in [source(name='海棠书屋'), source('https://m.po18xs.com'),
                     {**source(), 'exploreUrl': '玄幻::/a\n成人文学::/adult'}]:
            with self.subTest(item=item['bookSourceName']):
                self.assertEqual(self.policy.audit_source(item)['decision'], 'block')

    def test_content_prohibition_is_not_an_adult_promotion(self):
        item = {**source(), 'bookSourceComment': '严禁上传色情小说。拒绝传播成人内容。'}
        self.assertEqual(self.policy.audit_source(item)['decision'], 'pass')
        item['bookSourceComment'] = '成人文学，禁止未成年人访问'
        self.assertEqual(self.policy.audit_source(item)['decision'], 'block')

    def test_program_code_is_not_prose(self):
        item = {**source(), 'searchUrl': '/search?sex_flag=nan&q={{key}}',
                'ruleExplore': {'url': '@js: var sex_flag = 1;'}}
        self.assertEqual(self.policy.detect_adult_risks(item), [])

    def test_formatting_does_not_replace_readability_evidence(self):
        item = source(name='Example Reader')
        accepted, rejected = self.policy.screen_source(item)
        self.assertIsNotNone(accepted)
        self.assertIsNone(rejected)

    def test_original_name_is_audited_before_normalization(self):
        item = {**source(), 'originalName': '御书屋'}
        self.assertEqual(self.policy.audit_source(item)['decision'], 'block')

    def test_ambiguous_promotion_requires_review(self):
        item = {**source(), 'bookSourceComment': '擦边福利专区'}
        self.assertEqual(self.policy.audit_source(item)['decision'], 'review')


if __name__ == '__main__':
    unittest.main()
