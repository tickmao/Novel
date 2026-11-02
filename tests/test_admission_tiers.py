import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from reading_validator import ProbeFailure
from runtime_validator import PROFILE, RuntimeReadingValidator
from source_health import eligibility
from source_policy import SourcePolicy
from test_maintenance_v2 import source
from runtime_config import VALIDATOR_VERSION, runtime_fingerprint

PROSE = ' A traveler crossed the ancient mountain and found a quiet village near the river. ' * 10


class Catalog:
    """A tiny site whose catalogue and chapter count are the test's variables."""

    def __init__(self, books=2, chapters=4, duplicate=False):
        self.books, self.chapters, self.duplicate = books, chapters, duplicate

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def request(self, op, **kwargs):
        base = 'https://books.example'
        if op == 'search':
            return [{'name': f'Book {index}', 'bookUrl': f'{base}/book/{index}'}
                    for index in range(self.books)]
        if op == 'book':
            return {'name': 'Book', 'tocUrl': kwargs['url'] + '/toc'}
        if op == 'toc':
            return [{'title': f'Chapter {index}', 'url': kwargs['url'] + f'/{index}'}
                    for index in range(self.chapters)]
        prefix = 'same' if self.duplicate else kwargs['url']
        return {'content': prefix + PROSE}


class AdmissionTierTests(unittest.IsolatedAsyncioTestCase):
    def validator(self, **options):
        return RuntimeReadingValidator(SourcePolicy(ROOT), session_factory=lambda payload: Catalog(**options))

    async def test_one_readable_book_is_enough(self):
        result = await self.validator(books=1).probe(source())

        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['tier'], 'usable')
        self.assertEqual(result['sample'], {'books': 1, 'chapters': 2})

    async def test_two_readable_books_raise_the_badge(self):
        result = await self.validator(books=2).probe(source())

        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['tier'], 'stable')
        self.assertEqual(result['sample'], {'books': 2, 'chapters': 4})

    async def test_a_second_book_that_fails_cannot_write_off_the_source(self):
        """The second book is only a badge, so its failure must not cost the source."""

        class BrokenSecondBook(Catalog):
            async def request(self, op, **kwargs):
                if op == 'book' and kwargs['url'].endswith('/book/1'):
                    raise ProbeFailure('book_empty', 'The second book has no catalog')
                return await super().request(op, **kwargs)

        validator = RuntimeReadingValidator(SourcePolicy(ROOT),
                                            session_factory=lambda payload: BrokenSecondBook(books=2))
        result = await validator.probe(source())

        self.assertEqual(result['status'], 'valid')
        self.assertEqual(result['tier'], 'usable')

    async def test_a_single_chapter_is_not_reading_evidence(self):
        """One chapter cannot prove the site serves distinct pages."""
        result = await self.validator(books=1, chapters=1).probe(source())

        self.assertNotEqual(result['status'], 'valid')
        self.assertEqual(result['kind'], 'toc_empty')

    async def test_a_duplicate_template_still_fails_at_one_book(self):
        result = await self.validator(books=1, duplicate=True).probe(source())

        self.assertNotEqual(result['status'], 'valid')
        self.assertEqual(result['kind'], 'content_duplicate')

    async def test_an_empty_search_is_still_a_search_failure(self):
        result = await self.validator(books=0).probe(source())

        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['kind'], 'search_empty')


class TierEligibilityTests(unittest.TestCase):
    def test_a_usable_sample_is_eligible(self):
        version = {'audit': {'decision': 'pass', 'complete': True, 'policy_version': 'p',
                             'checked_at': '2026-11-01T00:00:00+00:00'},
                   'validation': {'validator_version': VALIDATOR_VERSION,
                                  'runtime_fingerprint': runtime_fingerprint(),
                                  'status': 'valid',
                                  'sample': {'books': 1, 'chapters': 2},
                                  'last_deep_success_at': '2026-11-01T00:00:00+00:00'}}

        self.assertEqual(eligibility(version, 'p'), 'ready')

    def test_one_chapter_is_not_eligible(self):
        version = {'audit': {'decision': 'pass', 'complete': True, 'policy_version': 'p',
                             'checked_at': '2026-11-01T00:00:00+00:00'},
                   'validation': {'validator_version': VALIDATOR_VERSION,
                                  'runtime_fingerprint': runtime_fingerprint(),
                                  'status': 'valid',
                                  'sample': {'books': 1, 'chapters': 1},
                                  'last_deep_success_at': '2026-11-01T00:00:00+00:00'}}

        self.assertIsNone(eligibility(version, 'p'))

    def test_the_profile_documents_the_tier(self):
        self.assertEqual(PROFILE['required_books'], 1)
        self.assertEqual(PROFILE['stable_books'], 2)
        self.assertEqual(PROFILE['chapters_per_book'], 2)


if __name__ == '__main__':
    unittest.main()
