import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from content_audit import ContentAudit
from reading_validator import Response
from source_collector import collect_into
from source_store import SourceStore, digest, source_id, source_payload
from submissions import submission_input
from test_maintenance_v2 import source


class ContextTests(unittest.TestCase):
    def test_ambiguous_words_keep_context_and_all_sampled_pages(self):
        audit = ContentAudit(json.loads((ROOT / 'config/content_audit.json').read_text()))
        result = audit.check(source(), [{'kind': 'book', 'url': 'https://books.example/a', 'text': '文章讨论色情一词的定义'},
                                        {'kind': 'book', 'url': 'https://books.example/b', 'text': 'A clean introduction'}])
        self.assertEqual(result['decision'], 'review')
        self.assertIn('讨论', result['evidence'][0]['context'])
        self.assertEqual(result['evidence'][0]['url'], 'https://books.example/a')

    def test_review_cannot_follow_a_changed_source_revision(self):
        config = json.loads((ROOT / 'config/content_audit.json').read_text())
        item = {**source(), 'bookSourceComment': '擦边福利专区'}
        result = ContentAudit(config).check(item)
        review = {'source_id': source_id(item), 'payload_sha256': digest(source_payload(item)),
                  'evidence_hash': result['evidence_hash'], 'policy_version': result['policy_version'],
                  'decision': 'pass', 'reason': 'Reviewed field context', 'reviewer': 'fixture'}
        auditor = ContentAudit(config, [review])
        self.assertEqual(auditor.check(item)['decision'], 'pass')
        self.assertEqual(auditor.check({**item, 'searchUrl': '/new'})['decision'], 'review')

    def test_submission_envelope_preserves_the_embedded_source(self):
        values, url = submission_input({'name': 'Example', 'content': [source()], 'url': None})
        self.assertEqual(values, [source()])
        self.assertIsNone(url)


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_feed_is_incremental_and_records_channel(self):
        class Client:
            calls = 0
            async def fetch(self, request):
                self.calls += 1
                return Response(request['url'], json.dumps([source()]), headers={'ETag': 'v1'})
        with tempfile.TemporaryDirectory() as tmp:
            store = SourceStore(Path(tmp))
            config = {'source_websites': [{'url': 'https://feed.example/sources.json', 'mode': 'json'}]}
            client = Client()
            first = await collect_into(store, client, config)
            second = await collect_into(store, client, config)
            self.assertEqual(first['new_versions'], 1)
            self.assertEqual(second['sources'], 0)
            self.assertEqual(client.calls, 1)
            record = next(store.records())
            version = record['versions'][record['latest_revision']]
            self.assertEqual(version['provenance'][0]['channel'], config['source_websites'][0]['url'])

    async def test_github_discovery_reads_subdirectories(self):
        class Client:
            async def fetch(self, request):
                url = request['url']
                if url.endswith('/contents/'):
                    value = [{'type': 'dir', 'path': 'sources', 'name': 'sources'}]
                elif url.endswith('/contents/sources'):
                    value = [{'type': 'file', 'name': 'books.json', 'download_url': 'https://feed.example/books.json'}]
                else:
                    value = [source()]
                return Response(url, json.dumps(value))
        with tempfile.TemporaryDirectory() as tmp:
            store = SourceStore(Path(tmp))
            result = await collect_into(store, Client(), {'github_repos': [{'name': 'fixture/sources'}]})
            self.assertEqual(result['sources'], 1)


if __name__ == '__main__':
    unittest.main()
