import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from reading_validator import ReadingValidator, Response, public_url, ProbeFailure
from source_policy import SourcePolicy
from static_rules import document, nodes, text, UnsupportedRule
from test_maintenance_v2 import source


class Transport:
    def __init__(self, body=None):
        self.calls = []
        self.body = body or ('春日山间的微风吹过古老城墙，少年背起行囊走向远方，沿着清澈河流寻找传说中的城市。' * 12)

    async def fetch(self, request):
        url = request['url']
        self.calls.append(url)
        if '/search?' in url:
            value = '<div class="book"><a href="/book">斗罗大陆</a></div>'
        elif url.endswith('/book'):
            value = '<h1>斗罗大陆</h1><div class="chapter"><a href="/chapter">第一章</a></div>'
        elif url.endswith('/chapter'):
            value = '<div id="content">' + self.body + '</div>'
        else:
            raise AssertionError(url)
        return Response(url, value)


class ReadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_html_response_is_invalid_without_raising(self):
        class EmptyTransport:
            async def fetch(self, request):
                return Response(request['url'], '  \n<!-- Empty response -->')

        result = await ReadingValidator(EmptyTransport(), SourcePolicy(ROOT)).probe(source())
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['kind'], 'response_empty')

    async def test_reading_chain_and_audit_are_required(self):
        client = Transport()
        result = await ReadingValidator(client, SourcePolicy(ROOT)).probe(source())
        self.assertEqual(result['status'], 'valid')
        self.assertTrue(result['audit']['complete'])
        self.assertEqual(result['kind'], 'reading')
        self.assertEqual(len(client.calls), 4)

    async def test_empty_chapter_is_not_valid(self):
        result = await ReadingValidator(Transport('暂无正文'), SourcePolicy(ROOT)).probe(source())
        self.assertEqual(result['status'], 'invalid')

    async def test_js_is_unsupported_without_a_homepage_fallback(self):
        client = Transport()
        item = source()
        item['ruleToc']['chapterUrl'] = '@js:result'
        result = await ReadingValidator(client, SourcePolicy(ROOT)).probe(item)
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(client.calls, [])

    async def test_dynamic_adult_evidence_prevents_publishing(self):
        result = await ReadingValidator(Transport('成人小说专区' * 100), SourcePolicy(ROOT)).probe(source())
        self.assertEqual(result['status'], 'review')
        self.assertNotEqual(result['audit']['decision'], 'pass')

    async def test_adult_book_page_uses_blocked_validation_status(self):
        class AdultTransport(Transport):
            async def fetch(self, request):
                response = await super().fetch(request)
                if request['url'].endswith('/book'):
                    response.text += '<nav>成人小说专区</nav>'
                return response

        result = await ReadingValidator(AdultTransport(), SourcePolicy(ROOT)).probe(source())
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['audit']['decision'], 'block')


class RuleTests(unittest.TestCase):
    def test_css_legacy_xpath_and_json(self):
        doc = document('<div class="books"><a href="/one">One</a><a href="/two">Two</a></div>')
        self.assertEqual(text(doc, 'class.books@tag.a.1@href'), '/two')
        self.assertEqual(text(doc, '@XPath://a[1]/@href'), '/one')
        self.assertEqual(text({'title': 'Book'}, '$.title'), 'Book')

    def test_private_network_targets_are_rejected(self):
        for url in ['http://127.0.0.1', 'http://[::1]', 'http://169.254.169.254', 'file:///etc/passwd']:
            with self.assertRaises(ProbeFailure):
                public_url(url)


if __name__ == '__main__':
    unittest.main()
