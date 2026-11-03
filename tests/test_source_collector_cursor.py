import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from reading_validator import Response
from source_collector import collect_into, is_source_file
from source_store import SourceStore
from test_maintenance_v2 import source


class ContentsTransport:
    """Answers the repository listing API and serves each file once."""

    def __init__(self, files):
        self.files = files
        self.downloads = []

    async def fetch(self, request):
        url = request['url']
        if '/contents/' in url:
            entries = [{'type': 'file', 'name': name, 'path': name,
                        'download_url': f'https://raw.example/{name}'} for name in self.files]
            return Response(url, json.dumps(entries))
        name = url.rsplit('/', 1)[1]
        self.downloads.append(name)
        return Response(url, json.dumps([source(f'https://{name}.example')]))


def config(max_files=2, patterns=('source',)):
    return {'github_repos': [{'name': 'owner/repo', 'file_patterns': list(patterns),
                              'max_files': max_files}]}


class SourceFileTests(unittest.TestCase):
    def test_a_json_file_in_the_root_is_a_source_file(self):
        self.assertTrue(is_source_file({'name': 'shuyuan.json', 'path': 'shuyuan.json'}))

    def test_an_issue_template_is_not_a_source_file(self):
        item = {'name': '03-book.json', 'path': '.github/ISSUE_TEMPLATE/03-book.json'}

        self.assertFalse(is_source_file(item))

    def test_tooling_manifests_are_not_source_files(self):
        for name in ('package.json', 'manifest.json', 'tsconfig.json'):
            with self.subTest(name=name):
                self.assertFalse(is_source_file({'name': name, 'path': name}))

    def test_a_pattern_match_does_not_need_the_json_suffix(self):
        self.assertTrue(is_source_file({'name': 'shuyuan.txt', 'path': 'shuyuan.txt'}, ('shuyuan',)))
        self.assertFalse(is_source_file({'name': 'notes.txt', 'path': 'notes.txt'}, ('shuyuan',)))


class RepoCursorTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, store, transport, **options):
        return await collect_into(store, transport, config(**options), deadline=None, save=True)

    async def test_a_repository_is_walked_over_successive_runs(self):
        files = ['a.json', 'b.json', 'c.json', 'd.json']
        with tempfile.TemporaryDirectory() as tmp:
            store = SourceStore(Path(tmp))
            transport = ContentsTransport(files)

            await self.collect(store, transport, max_files=2)
            await self.collect(SourceStore(Path(tmp)), transport, max_files=2)

            self.assertEqual(transport.downloads, ['a.json', 'b.json', 'c.json', 'd.json'])

    async def test_the_cursor_wraps_after_the_last_file(self):
        files = ['a.json', 'b.json', 'c.json']
        with tempfile.TemporaryDirectory() as tmp:
            transport = ContentsTransport(files)
            for _ in range(3):
                await self.collect(SourceStore(Path(tmp)), transport, max_files=1)

            self.assertEqual(transport.downloads, ['a.json', 'b.json', 'c.json'])
            self.assertEqual(SourceStore(Path(tmp)).state['repo_cursors']['owner/repo'], 0)

    async def test_every_file_lands_in_the_store(self):
        files = ['a.json', 'b.json', 'c.json']
        with tempfile.TemporaryDirectory() as tmp:
            transport = ContentsTransport(files)
            for _ in range(2):
                await self.collect(SourceStore(Path(tmp)), transport, max_files=2)

            self.assertEqual(SourceStore(Path(tmp)).manifest['count'], 3)

    async def test_an_unchanged_file_is_not_downloaded_twice_in_the_same_day(self):
        files = ['a.json']
        with tempfile.TemporaryDirectory() as tmp:
            transport = ContentsTransport(files)
            await self.collect(SourceStore(Path(tmp)), transport, max_files=2)
            await self.collect(SourceStore(Path(tmp)), transport, max_files=2)

            self.assertEqual(transport.downloads, ['a.json'])


if __name__ == '__main__':
    unittest.main()
