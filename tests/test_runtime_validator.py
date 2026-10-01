import asyncio
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from runtime_validator import RuntimeReadingValidator, EngineFailure
from source_policy import SourcePolicy
from test_maintenance_v2 import source


class FakeEngine:
    def __init__(self, payload, fail=None, adult=False, duplicate=False):
        self.payload, self.fail, self.adult, self.duplicate = payload, fail, adult, duplicate

    async def __aenter__(self):
        if self.fail:
            raise EngineFailure(self.fail, 'fixture failure')
        return self

    async def __aexit__(self, *_):
        pass

    async def request(self, op, **kwargs):
        base = 'https://books.example'
        if op == 'search':
            return [{'name': f'Book {i}', 'bookUrl': f'{base}/book/{i}'} for i in range(2)]
        if op == 'book':
            return {'name': 'Book', 'tocUrl': kwargs['url'] + '/toc'}
        if op == 'toc':
            return [{'title': f'Chapter {i}', 'url': kwargs['url'] + f'/{i}'} for i in range(4)]
        if self.adult:
            return {'content': '成人文学专区 ' + 'context ' * 50}
        prefix = 'same' if self.duplicate else kwargs['url']
        return {'content': prefix + ' A traveler crossed the ancient mountain and found a quiet village near the river. ' * 10}


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def validator(self, **options):
        return RuntimeReadingValidator(SourcePolicy(ROOT), session_factory=lambda p: FakeEngine(p, **options))

    async def test_two_books_and_four_distinct_chapters_are_required(self):
        item = source()
        before = deepcopy(item)
        result = await self.validator().probe(item)
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['sample'], {'books': 2, 'chapters': 4})
        self.assertEqual(item, before)

    async def test_engine_failure_is_not_a_site_failure(self):
        result = await self.validator(fail='engine_crash').probe(source())
        self.assertEqual((result['status'], result['kind']), ('unverified', 'engine_crash'))

    async def test_duplicate_chapter_template_does_not_pass(self):
        result = await self.validator(duplicate=True).probe(source())
        self.assertEqual(result['kind'], 'content_duplicate')

    async def test_adult_evidence_stops_admission(self):
        result = await self.validator(adult=True).probe(source())
        self.assertIn(result['status'], ('review', 'blocked'))
        self.assertEqual(result['kind'], 'content_audit')

    async def test_browser_rules_remain_unsupported(self):
        result = await self.validator().probe({**source(), 'webView': True})
        self.assertEqual(result['status'], 'unsupported')

    async def test_light_check_does_not_issue_reading_evidence(self):
        result = await self.validator().probe(source(), 'light')
        self.assertEqual(result['kind'], 'search')
        self.assertNotIn('sample', result)
        self.assertFalse(result['audit']['complete'])


if __name__ == '__main__':
    unittest.main()
