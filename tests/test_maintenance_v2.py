import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from source_store import SourceStore, atomic_bundle
from source_policy import SourcePolicy


def source(url='https://books.example', name='测试书站'):
    return {
        'bookSourceName': name, 'bookSourceUrl': url, 'bookSourceType': 0,
        'searchUrl': '/search?q={{key}}',
        'ruleSearch': {'bookList': '.book', 'name': 'a@text', 'bookUrl': 'a@href'},
        'ruleBookInfo': {'name': 'h1@text'},
        'ruleToc': {'chapterList': '.chapter', 'chapterName': 'a@text', 'chapterUrl': 'a@href'},
        'ruleContent': {'content': '#content@text'},
    }


class StoreTests(unittest.TestCase):
    def test_revisions_and_provenance_survive_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SourceStore(Path(tmp))
            old = source()
            first = store.ingest(old, {'provider': 'yckceo', 'item_id': '42'})
            store.ingest(old, {'provider': 'github', 'url': 'https://github.com/example/repo'})
            new = {**old, 'searchUrl': '/find?q={{key}}'}
            second = store.ingest(new, {'provider': 'yckceo', 'item_id': '42'})
            store.save()
            loaded = SourceStore(Path(tmp))
            record = loaded.get(first[0])
            self.assertEqual(first[0], second[0])
            self.assertNotEqual(first[1], second[1])
            self.assertEqual(len(record['versions']), 2)
            self.assertEqual(len(record['versions'][first[1]]['provenance']), 2)
            self.assertEqual(record['versions'][first[1]]['source'], old)

    def test_bundle_restores_all_files_after_partial_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.json', Path(tmp) / 'b.json'
            a.write_bytes(b'old-a')
            b.write_bytes(b'old-b')
            import os
            replace = os.replace
            calls = 0

            def fail_once(src, dst):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError('injected failure')
                return replace(src, dst)

            with patch('source_store.os.replace', side_effect=fail_once):
                with self.assertRaises(OSError):
                    atomic_bundle({a: b'new-a', b: b'new-b'})
            self.assertEqual(a.read_bytes(), b'old-a')
            self.assertEqual(b.read_bytes(), b'old-b')


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.policy = SourcePolicy(ROOT)

    def test_genres_and_query_parameters_are_not_adult_evidence(self):
        item = source(name='红薯阅读')
        item.update(searchUrl='/search?sex_flag=nan&q={{key}}', exploreUrl='纯爱::/a\n女尊::/b\n耽美::/c')
        self.assertEqual(self.policy.audit_source(item)['decision'], 'pass')

    def test_adult_category_is_blocked_and_ambiguous_text_is_reviewed(self):
        self.assertEqual(self.policy.audit_source({**source(), 'exploreUrl': '成人文学::/adult'})['decision'], 'block')
        self.assertEqual(self.policy.audit_source({**source(), 'bookSourceComment': '擦边福利专区'})['decision'], 'review')

    def test_repeated_scoring_is_idempotent(self):
        first = self.policy.enrich_source(source())
        self.assertEqual(first['selectionScore'], self.policy.enrich_source(first)['selectionScore'])


if __name__ == '__main__':
    unittest.main()
