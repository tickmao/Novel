"""Validate exact revisions through a disposable Legado JVM worker."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import tempfile
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from reading_validator import public_url, ProbeFailure
from runtime_config import ENGINE_COMMIT, VALIDATOR_VERSION, runtime_fingerprint
from source_store import digest, source_payload, utcnow
from static_rules import visible_text
from content_audit import category_text


class EngineFailure(Exception):
    def __init__(self, kind, message):
        self.kind = kind
        super().__init__(message)


def engine_command():
    raw = os.environ.get('NOVEL_ENGINE_COMMAND')
    if raw:
        value = json.loads(raw)
        if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
            raise ValueError('NOVEL_ENGINE_COMMAND must be a JSON argument array')
        return value
    image = os.environ.get('NOVEL_ENGINE_IMAGE', '')
    if not re.fullmatch(r'(?:[\w./:-]+@)?sha256:[0-9a-f]{64}', image):
        raise EngineFailure('engine_unavailable', 'Set a pinned NOVEL_ENGINE_IMAGE or NOVEL_ENGINE_COMMAND')
    return ['docker', 'run', '--rm', '-i', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--pids-limit=128', '--memory=512m', '--cpus=1',
            '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=128m', image]


class EngineSession:
    def __init__(self, source, *, fixtures=None, timeout=30):
        self.source, self.fixtures, self.timeout = source, fixtures, timeout
        self.process = None
        self.container = None
        self.temp = None
        self.stderr_task = None
        self.startup_error = bytearray()
        self.ready = False

    async def drain_stderr(self):
        while chunk := await self.process.stderr.read(4096):
            if not self.ready and len(self.startup_error) < 2000:
                self.startup_error.extend(chunk[:2000 - len(self.startup_error)])

    async def __aenter__(self):
        try:
            command = list(engine_command())
            if command[:2] == ['docker', 'run']:
                self.container = 'novel-probe-' + uuid.uuid4().hex
                command[2:2] = ['--name', self.container]
            self.temp = tempfile.TemporaryDirectory(prefix='novel-worker-')
            env = {key: os.environ[key] for key in ('PATH', 'JAVA_HOME', 'SYSTEMROOT') if key in os.environ}
            env.update(TMPDIR=self.temp.name, LANG='en_US.UTF-8')
            self.process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env, cwd=self.temp.name,
                start_new_session=True, limit=4 * 1024 * 1024,
            )
            self.stderr_task = asyncio.create_task(self.drain_stderr())
            request = {'source': self.source}
            if self.fixtures is not None:
                request['fixtures'] = self.fixtures
            response = await self.exchange(request)
            if response.get('engine_commit') != ENGINE_COMMIT or response.get('protocol') != 1:
                raise EngineFailure('engine_protocol', 'Worker identity or protocol mismatch')
            self.ready = True
            return self
        except (OSError, ValueError) as exc:
            await self.close()
            raise EngineFailure('engine_unavailable', type(exc).__name__) from exc
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        if self.process and self.process.returncode is None:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.process.wait()
        if self.container:
            try:
                cleanup = await asyncio.create_subprocess_exec(
                    'docker', 'rm', '-f', self.container, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(cleanup.wait(), 10)
            except (OSError, asyncio.TimeoutError):
                pass
        if self.temp:
            self.temp.cleanup()
        if self.stderr_task:
            await self.stderr_task

    async def exchange(self, request):
        try:
            self.process.stdin.write((json.dumps(request, ensure_ascii=False) + '\n').encode())
            await self.process.stdin.drain()
            line = await asyncio.wait_for(self.process.stdout.readline(), self.timeout)
            if not line:
                raise await self.closed_error()
            response = json.loads(line)
            if not isinstance(response, dict) or 'ok' not in response:
                raise ValueError('Invalid response')
            return response
        except asyncio.TimeoutError as exc:
            raise EngineFailure('engine_timeout', 'Worker request exceeded its budget') from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise await self.closed_error() from exc
        except (ValueError, OSError) as exc:
            raise EngineFailure('engine_protocol', type(exc).__name__) from exc

    async def closed_error(self):
        try:
            await asyncio.wait_for(self.process.wait(), 2)
            if self.stderr_task:
                await asyncio.wait_for(asyncio.shield(self.stderr_task), 2)
        except asyncio.TimeoutError:
            return EngineFailure('engine_protocol', 'Worker closed its protocol stream')
        detail = self.startup_error.decode(errors='replace') if not self.ready else ''
        return EngineFailure('engine_crash', f'Worker exited with status {self.process.returncode}: {detail}')

    async def request(self, op, **kwargs):
        response = await self.exchange({'op': op, **kwargs})
        if response['ok']:
            return response.get('value')
        message = response.get('error', '')
        kind = 'rule_error'
        if '429' in message:
            kind = 'rate_limit'
        elif re.search(r'HTTP 5\d\d', message):
            kind = 'server_error'
        elif re.search(r'timeout|timed out', message, re.I):
            kind = 'timeout'
        elif re.search(r'ConnectException|UnknownHost|无法解析|连接|DNS', message + response.get('error_type', ''), re.I):
            kind = 'network'
        elif re.search(r'Unsupported|not supported|不支持|WebView|startBrowser', message, re.I):
            kind = 'unsupported'
        raise ProbeFailure(kind, message[:240])


class RuntimeReadingValidator:
    def __init__(self, policy, *, session_factory=EngineSession, concurrency=4, keywords=None):
        self.policy, self.session_factory = policy, session_factory
        self.limit = asyncio.Semaphore(concurrency)
        self.sites = defaultdict(lambda: asyncio.Semaphore(2))
        self.keywords = keywords or ['斗罗大陆', '凡人修仙传', '三国演义', '遮天', '西游记', '完美世界']

    async def probe(self, source, mode='deep'):
        audit = self.policy.audit_source(source)
        result = {'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION, 'mode': mode,
                  'runtime_fingerprint': runtime_fingerprint(),
                  'engine': {'commit': ENGINE_COMMIT}, 'audit': audit, 'stages': []}
        if audit['decision'] != 'pass':
            return {**result, 'status': 'blocked' if audit['decision'] == 'block' else 'review', 'kind': 'content_audit'}
        start = time.monotonic()
        try:
            identity = str(source.get('bookSourceUrl', ''))
            if not identity:
                raise ProbeFailure('invalid_url', 'Source identity is missing')
            if identity.startswith(('http://', 'https://')):
                public_url(identity)
            if source.get('enabled') is False or str(source.get('bookSourceType', 0)) != '0':
                raise ProbeFailure('disabled', 'Only enabled novel sources are eligible')
            if source.get('webView') or re.search(r'startBrowserAwait|startBrowser\(', json.dumps(source)):
                raise ProbeFailure('unsupported', 'Interactive browser rules need a separate runtime')
            async with self.limit, self.sites[urlsplit(identity).hostname or identity]:
                async with asyncio.timeout(240):
                    async with self.session_factory(source_payload(source)) as engine:
                        result.update(await self._probe(engine, source, mode, result['stages']))
        except EngineFailure as exc:
            result.update(status='unverified', kind=exc.kind, error=str(exc))
        except ProbeFailure as exc:
            status = 'transient' if exc.kind in ('network', 'timeout', 'server_error', 'rate_limit') else 'unverified'
            if exc.kind == 'unsupported':
                status = 'unsupported'
            if exc.kind == 'content_audit_incomplete':
                status = 'review'
            if exc.kind in ('content_empty', 'content_locked', 'content_duplicate', 'disabled', 'invalid_url'):
                status = 'invalid'
            result.update(status=status, kind=exc.kind, error=str(exc), retry_after=3600 if exc.kind == 'rate_limit' else 0)
        except asyncio.TimeoutError:
            result.update(status='unverified', kind='engine_timeout', error='Source exceeded its budget')
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            result.update(status='unverified', kind='engine_protocol', error=type(exc).__name__)
        result['response_ms'] = round((time.monotonic() - start) * 1000)
        if mode == 'spot':
            result['mode'] = 'deep'
        return result

    async def _probe(self, engine, source, mode, stages):
        pages, books, seen_books, checked_books = [], [], set(), []

        def audit_page(kind, url, value):
            text = visible_text(value) if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            pages.append({'kind': kind, 'url': url, 'text': text})
            review = self.policy.audit_source(source, pages)
            if review['decision'] != 'pass':
                return {'status': 'blocked' if review['decision'] == 'block' else 'review',
                        'kind': 'content_audit', 'audit': review}
            return None

        explore = str(source.get('exploreUrl', '')).strip()
        if explore.startswith(('@js:', '<js>')):
            try:
                categories = await engine.request('categories')
            except ProbeFailure as exc:
                raise ProbeFailure('content_audit_incomplete', 'Dynamic discovery categories need review') from exc
            if flagged := audit_page('categories', source['bookSourceUrl'], category_text(categories)):
                return flagged

        previous = [] if mode == 'spot' else source.get('_health', {}).get('reading_books', [])
        offset = (int(digest(source.get('bookSourceUrl', ''))[:8], 16)
                  + datetime.now(timezone.utc).date().toordinal()) % len(self.keywords)
        keywords = list(dict.fromkeys([b['name'] for b in previous if b.get('name')]
                                     + self.keywords[offset:] + self.keywords[:offset]))
        for keyword in keywords[:6]:
            items = await engine.request('search', keyword=keyword)
            if not isinstance(items, list):
                raise ValueError('Search response must be an array')
            stages.append({'stage': 'search', 'keyword': keyword, 'count': len(items)})
            if flagged := audit_page('search', source['bookSourceUrl'], items):
                return flagged
            for item in items[:20]:
                url, name = item.get('bookUrl'), item.get('name')
                if url and name and url not in seen_books:
                    public_url(url)
                    seen_books.add(url)
                    books.append(item)
            if len(books) >= (1 if mode == 'light' else 2):
                break
        if len(books) < (1 if mode == 'light' else 2):
            raise ProbeFailure('search_empty', 'Too few distinct books were extracted; failure is unconfirmed')
        if mode == 'light':
            return {'status': 'valid', 'kind': 'search', 'book': books[0]}
        hashes = set()
        for book in books[:2]:
            details = await engine.request('book', url=book['bookUrl'])
            stages.append({'stage': 'book', 'url': book['bookUrl']})
            if not details.get('name') or not details.get('tocUrl'):
                raise ProbeFailure('book_empty', 'Book details are incomplete')
            if flagged := audit_page('book', book['bookUrl'], details):
                return flagged
            public_url(details['tocUrl'])
            chapters = await engine.request('toc', url=details['tocUrl'])
            chapters = list({item['url']: item for item in chapters if item.get('url')}.values())
            stages.append({'stage': 'toc', 'url': details['tocUrl'], 'count': len(chapters)})
            if flagged := audit_page('toc', details['tocUrl'], chapters):
                return flagged
            if len(chapters) < 2:
                raise ProbeFailure('toc_empty', 'Too few distinct chapters were extracted')
            chapter_index = (datetime.now(timezone.utc).date().toordinal() % (len(chapters) - 1) + 1
                             if mode == 'spot' else len(chapters) // 2)
            selected = [chapters[0], chapters[chapter_index]]
            for chapter in selected:
                public_url(chapter['url'])
                value = await engine.request('content', url=chapter['url'], book_name=book['name'])
                prose = visible_text(value.get('content', ''))
                raw = value.get('rawContent') or prose
                if flagged := audit_page('content', chapter['url'], raw):
                    return flagged
                if len(re.sub(r'\s', '', prose)) < 200 or len(set(prose)) < 20:
                    raise ProbeFailure('content_empty', 'Chapter text is empty or too short')
                if re.search(r'请(?:先)?登录后(?:阅读|查看)|购买本章|章节内容不存在|暂无正文|验证码|Access denied', prose, re.I):
                    raise ProbeFailure('content_locked', 'Chapter contains an access gate')
                content_hash = digest(re.sub(r'\s', '', prose))
                if content_hash in hashes:
                    raise ProbeFailure('content_duplicate', 'Different chapters returned identical content')
                hashes.add(content_hash)
                stages.append({'stage': 'content', 'url': chapter['url'], 'length': len(prose), 'sha256': content_hash})
            checked_books.append({'name': book['name'], 'url': book['bookUrl']})
        return {'status': 'valid', 'kind': 'reading', 'audit': self.policy.audit_source(source, pages),
                'books': checked_books, 'sample': {'books': len(checked_books), 'chapters': len(hashes)}}
