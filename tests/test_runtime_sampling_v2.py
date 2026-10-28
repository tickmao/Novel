import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from reading_validator import ProbeFailure
from runtime_validator import PROFILE, RuntimeReadingValidator
from source_policy import SourcePolicy

BASE = 'https://books.example'


def prose(seed):
    return ''.join(chr(0x4e00 + (seed + index) % 2000) for index in range(240))


def chapter_source(explore=''):
    return {'bookSourceName': 'Fixture', 'bookSourceUrl': BASE, 'bookSourceType': 0,
            'searchUrl': '/search?q={{key}}', 'ruleSearch': {}, 'ruleBookInfo': {},
            'ruleToc': {}, 'ruleContent': {}, 'exploreUrl': explore}


class FakeEngine:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def request(self, operation, **kwargs):
        key = (operation, kwargs.get('keyword') or kwargs.get('url'))
        self.calls.append(key)
        value = self.responses.get(key)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AssertionError(f'unexpected probe request {key}')
        return value

    def count(self, operation):
        return sum(1 for name, _ in self.calls if name == operation)


def books(count):
    return [{'name': f'Book {index}', 'bookUrl': f'{BASE}/book/{index}'} for index in range(count)]


def readable(responses, indexes, chapters=8, failing_chapters=(), short_chapters=()):
    for index in indexes:
        url = f'{BASE}/book/{index}'
        responses[('book', url)] = {'name': f'Book {index}', 'tocUrl': f'{url}/toc'}
        responses[('toc', f'{url}/toc')] = [
            {'name': f'Chapter {chapter}', 'url': f'{url}/chapter/{chapter}'} for chapter in range(chapters)]
        for chapter in range(chapters):
            chapter_url = f'{url}/chapter/{chapter}'
            if chapter in failing_chapters:
                responses[('content', chapter_url)] = ProbeFailure('content_locked', 'Chapter contains an access gate')
            elif chapter in short_chapters:
                responses[('content', chapter_url)] = {'content': '太短'}
            else:
                responses[('content', chapter_url)] = {'content': prose(index * 10 + chapter)}
    return responses


class SamplingProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.validator = RuntimeReadingValidator(SourcePolicy(ROOT))

    async def test_a_failed_book_does_not_write_off_the_source(self):
        responses = {('search', None): None}
        responses = {('search', keyword): books(4) for keyword in self.validator._keywords(chapter_source(), 'deep')}
        readable(responses, [1, 2])
        responses[('toc', f'{BASE}/book/0/toc')] = ProbeFailure('toc_empty', 'Too few distinct chapters were extracted')
        responses[('book', f'{BASE}/book/0')] = {'name': 'Book 0', 'tocUrl': f'{BASE}/book/0/toc'}

        result = await self.validator._probe(FakeEngine(responses), chapter_source(), 'deep', [])
        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['sample'], {'books': 2, 'chapters': 4})

    async def test_an_unreadable_chapter_falls_through_to_another_index(self):
        keywords = self.validator._keywords(chapter_source(), 'deep')
        responses = {('search', keyword): books(2) for keyword in keywords}
        readable(responses, [0, 1], short_chapters=(0, 4))

        result = await self.validator._probe(FakeEngine(responses), chapter_source(), 'deep', [])
        self.assertEqual(result['sample'], {'books': 2, 'chapters': 4})

    async def test_content_thresholds_are_not_relaxed(self):
        keywords = self.validator._keywords(chapter_source(), 'deep')
        responses = {('search', keyword): books(2) for keyword in keywords}
        readable(responses, [0, 1], short_chapters=tuple(range(8)))
        with self.assertRaises(ProbeFailure) as caught:
            await self.validator._probe(FakeEngine(responses), chapter_source(), 'deep', [])
        self.assertEqual(caught.exception.kind, 'content_empty')
        self.assertEqual(PROFILE['min_content_chars'], 200)

    async def test_duplicate_chapter_text_is_still_rejected(self):
        keywords = self.validator._keywords(chapter_source(), 'deep')
        responses = {('search', keyword): books(2) for keyword in keywords}
        for index in range(2):
            url = f'{BASE}/book/{index}'
            responses[('book', url)] = {'name': f'Book {index}', 'tocUrl': f'{url}/toc'}
            responses[('toc', f'{url}/toc')] = [
                {'name': '一', 'url': f'{url}/chapter/0'}, {'name': '二', 'url': f'{url}/chapter/1'}]
            for chapter in range(2):
                responses[('content', f'{url}/chapter/{chapter}')] = {'content': prose(0)}
        with self.assertRaises(ProbeFailure) as caught:
            await self.validator._probe(FakeEngine(responses), chapter_source(), 'deep', [])
        self.assertEqual(caught.exception.kind, 'content_duplicate')

    async def test_keyword_plan_is_bounded_and_source_first(self):
        item = chapter_source()
        item['ruleSearch'] = {'checkKeyWord': '剑来'}
        keywords = self.validator._keywords(item, 'deep')
        self.assertEqual(keywords[0], '剑来')
        self.assertEqual(len(self.validator.keywords), 10)
        self.assertEqual(keywords, self.validator._keywords(item, 'deep'))

    async def test_discovery_titles_extend_the_search(self):
        item = chapter_source('@js:JSON.stringify([{title:"玄幻"}])')
        responses = {('categories', None): [{'title': '玄幻'}, {'title': '都市'}]}
        for keyword in self.validator._keywords(item, 'deep'):
            responses[('search', keyword)] = []
        responses[('search', '玄幻')] = books(2)
        readable(responses, [0, 1])

        engine = FakeEngine(responses)
        result = await self.validator._probe(engine, item, 'deep', [])
        self.assertEqual(result['status'], 'valid')
        self.assertIn(('search', '玄幻'), engine.calls)

    def test_category_keywords_are_bounded_and_deduplicated(self):
        value = [{'title': '玄幻'}, {'title': '玄幻'}, {'title': '都' * 20}, {'name': '都市'}, 'broken']
        self.assertEqual(RuntimeReadingValidator._category_keywords(value, []), ['玄幻', '都市'])
        self.assertEqual(RuntimeReadingValidator._category_keywords(value, ['玄幻']), ['都市'])
        self.assertEqual(RuntimeReadingValidator._category_keywords('not json', []), [])


if __name__ == '__main__':
    unittest.main()
