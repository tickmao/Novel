"""Search, catalog, and chapter checks without executing source JavaScript."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import aiohttp
from lxml import etree

from search_probe import build_search_request, is_blocked_page
from source_store import utcnow
from static_rules import UnsupportedRule, check_static, document, nodes, replace_text, text, visible_text

VALIDATOR_VERSION = 'static-reading-v2'
TRANSIENT = {'timeout', 'network', 'rate_limit', 'server_error'}


class ProbeFailure(Exception):
    def __init__(self, kind: str, message: str, retry_after=0):
        self.kind = kind
        self.retry_after = retry_after
        super().__init__(message)


def public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ProbeFailure('invalid_url', 'Expected a public HTTP URL')
    host = parsed.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ProbeFailure('invalid_url', 'Local hosts are not allowed')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ProbeFailure('invalid_url', 'Private addresses are not allowed')


class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self):
        self.resolver = aiohttp.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        answers = await self.resolver.resolve(host, port, family)
        if any(not ipaddress.ip_address(item['host']).is_global for item in answers):
            raise OSError('Private DNS result is not allowed')
        return answers

    async def close(self):
        await self.resolver.close()


@dataclass
class Response:
    url: str
    text: str
    status: int = 200
    headers: dict = field(default_factory=dict)


class HTTPClient:
    def __init__(self, concurrency=20, timeout=10, max_bytes=2 * 1024 * 1024):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.limit = asyncio.Semaphore(concurrency)
        self.host_limits = defaultdict(lambda: asyncio.Semaphore(2))
        self.session = None

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(limit=40, resolver=PublicResolver())
        self.session = aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.DummyCookieJar(), trust_env=True)
        return self

    async def __aexit__(self, *_):
        await self.session.close()

    async def fetch(self, request: dict) -> Response:
        url, method = request['url'], request.get('method', 'GET')
        if method not in ('GET', 'POST'):
            raise UnsupportedRule('Only GET and POST are supported')
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; NovelSourceCheck/2.0)', **request.get('headers', {})}
        headers = {k: v for k, v in headers.items() if k.lower() not in ('host', 'content-length')}
        body = request.get('body')
        if body is not None:
            headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')
            body = body.encode(request.get('charset', 'utf-8'))
        for _ in range(6):
            public_url(url)
            host = urlsplit(url).hostname
            try:
                async with self.limit, self.host_limits[host]:
                    async with self.session.request(method, url, headers=headers, data=body, allow_redirects=False,
                                                    timeout=aiohttp.ClientTimeout(total=self.timeout)) as response:
                        if response.status in (301, 302, 303, 307, 308):
                            target = urljoin(url, response.headers.get('Location', ''))
                            if urlsplit(target).netloc != urlsplit(url).netloc:
                                headers = {k: v for k, v in headers.items() if k.lower() not in ('authorization', 'cookie')}
                            if response.status == 303 or response.status in (301, 302) and method == 'POST':
                                method, body = 'GET', None
                            url = target
                            continue
                        if response.status == 429:
                            value = response.headers.get('Retry-After', '0')
                            try:
                                retry_after = max(0, int(value))
                            except ValueError:
                                from email.utils import parsedate_to_datetime
                                from datetime import datetime, timezone
                                try:
                                    retry_after = max(0, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
                                except (ValueError, TypeError):
                                    retry_after = 0
                            raise ProbeFailure('rate_limit', 'HTTP 429', retry_after)
                        if response.status >= 500:
                            raise ProbeFailure('server_error', f'HTTP {response.status}')
                        if response.status >= 400:
                            raise ProbeFailure('http_error', f'HTTP {response.status}')
                        data = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            data.extend(chunk)
                            if len(data) > self.max_bytes:
                                raise ProbeFailure('response_limit', 'Response exceeds the configured byte limit')
                        charset = response.charset
                        if not charset:
                            match = re.search(rb'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)', data[:8192])
                            charset = match.group(1).decode() if match else request.get('charset', 'utf-8')
                        try:
                            value = data.decode(charset, errors='replace')
                        except LookupError as exc:
                            raise UnsupportedRule('Unknown response charset') from exc
                        return Response(str(response.url), value, response.status, dict(response.headers))
            except asyncio.TimeoutError as exc:
                raise ProbeFailure('timeout', 'Request timed out') from exc
            except aiohttp.ClientConnectorCertificateError as exc:
                raise ProbeFailure('tls_error', 'TLS certificate check failed') from exc
            except aiohttp.ClientError as exc:
                raise ProbeFailure('network', type(exc).__name__) from exc
        raise ProbeFailure('redirect_limit', 'Too many redirects')


class ReadingValidator:
    def __init__(self, client, policy, keywords=None):
        self.client, self.policy = client, policy
        self.keywords = keywords or ['斗罗大陆', '凡人修仙传', '三国演义']

    def supports(self, source: dict):
        for key in ('searchUrl', 'header', 'ruleSearch', 'ruleBookInfo', 'ruleToc', 'ruleContent'):
            check_static(source.get(key, ''))
        if source.get('jsLib') or source.get('webView'):
            raise UnsupportedRule('External JavaScript or WebView is required')
        for section, fields in {'ruleSearch': ('bookList', 'name', 'bookUrl'),
                                'ruleToc': ('chapterList', 'chapterUrl'), 'ruleContent': ('content',)}.items():
            rules = source.get(section)
            if not isinstance(rules, dict) or any(not rules.get(field) for field in fields):
                raise UnsupportedRule(f'Missing static {section} rules')

    async def probe(self, source: dict, mode='deep') -> dict:
        start = time.monotonic()
        audit = self.policy.audit_source(source)
        result = {'checked_at': utcnow(), 'validator_version': VALIDATOR_VERSION, 'mode': mode, 'audit': audit}
        if audit['decision'] != 'pass':
            return {**result, 'status': 'blocked' if audit['decision'] == 'block' else 'review', 'kind': 'content_audit'}
        try:
            self.supports(source)
            if source.get('enabled') is False or str(source.get('bookSourceType', 0)) != '0':
                raise ProbeFailure('disabled', 'Source is disabled or is not a novel source')
            async with asyncio.timeout(90):
                result.update(await self._probe(source, mode))
        except UnsupportedRule as exc:
            result.update(status='unsupported', kind='unsupported', error=str(exc))
        except etree.ParserError:
            result.update(status='invalid', kind='response_empty', error='Response has no parseable HTML document')
        except ProbeFailure as exc:
            result.update(status='transient' if exc.kind in TRANSIENT else 'invalid', kind=exc.kind, error=str(exc), retry_after=exc.retry_after)
        except (asyncio.TimeoutError, TimeoutError):
            result.update(status='transient', kind='timeout', error='Source check timed out')
        except (ValueError, TypeError, LookupError, AttributeError) as exc:
            result.update(status='unsupported', kind='unsupported', error=type(exc).__name__)
        result['response_ms'] = round((time.monotonic() - start) * 1000)
        return result

    async def _probe(self, source, mode):
        pages = []
        default_request = build_search_request(source, self.keywords[0])
        if default_request is None:
            raise UnsupportedRule('Search request cannot be evaluated statically')

        async def fetch(request, kind):
            response = await self.client.fetch(request)
            if is_blocked_page(response.text):
                raise ProbeFailure('challenge', 'Challenge or verification page')
            pages.append({'kind': kind, 'url': response.url, 'text': visible_text(response.text)})
            audit = self.policy.audit_source(source, pages)
            if audit['decision'] != 'pass':
                return response, audit
            return response, None

        def request(url):
            return {**default_request, 'method': 'GET', 'url': url, 'body': None}

        book_url, book_name = '', ''
        search_rules = source['ruleSearch']
        for keyword in self.keywords:
            response, flagged = await fetch(build_search_request(source, keyword), 'search')
            if flagged:
                return {'status': 'blocked' if flagged['decision'] == 'block' else 'review', 'kind': 'content_audit', 'audit': flagged}
            doc = document(response.text)
            for item in nodes(doc, search_rules['bookList'])[:20]:
                name = text(item, search_rules['name']).strip()
                link = text(item, search_rules['bookUrl']).splitlines()
                if keyword in name and link:
                    book_name, book_url = name, urljoin(response.url, link[0])
                    break
            if book_url:
                break
        if not book_url:
            raise ProbeFailure('search_empty', 'No matching book was extracted')
        public_url(book_url)
        if mode == 'light':
            return {'status': 'valid', 'kind': 'search', 'book': {'name': book_name, 'url': book_url}}

        response, flagged = await fetch(request(book_url), 'book')
        if flagged:
            return {'status': 'blocked' if flagged['decision'] == 'block' else 'review', 'kind': 'content_audit', 'audit': flagged}
        book_doc = document(response.text)
        info_rules = source.get('ruleBookInfo') or {}
        if info_rules.get('init'):
            selected = nodes(book_doc, info_rules['init'])
            if not selected:
                raise ProbeFailure('book_empty', 'Book selector returned no data')
            book_doc = selected[0]
        toc_url = text(book_doc, info_rules['tocUrl']) if info_rules.get('tocUrl') else response.url
        response, flagged = await fetch(request(urljoin(response.url, toc_url)), 'toc')
        if flagged:
            return {'status': 'blocked' if flagged['decision'] == 'block' else 'review', 'kind': 'content_audit', 'audit': flagged}
        toc_doc = document(response.text)
        toc_rules = source['ruleToc']
        chapters = nodes(toc_doc, toc_rules['chapterList'])
        if not chapters:
            raise ProbeFailure('toc_empty', 'No chapters were extracted')
        chapter_url = ''
        for chapter in chapters[:10]:
            link = text(chapter, toc_rules['chapterUrl']).splitlines()
            if link and not link[0].startswith(('javascript:', '#')):
                chapter_url = urljoin(response.url, link[0])
                break
        if not chapter_url:
            raise ProbeFailure('toc_empty', 'No chapter URL was extracted')
        response, flagged = await fetch(request(chapter_url), 'content')
        if flagged:
            return {'status': 'blocked' if flagged['decision'] == 'block' else 'review', 'kind': 'content_audit', 'audit': flagged}
        content = visible_text(text(document(response.text), source['ruleContent']['content']))
        if source['ruleContent'].get('replaceRegex'):
            content = replace_text(content, source['ruleContent']['replaceRegex'])
        prose = re.sub(r'https?://\S+', '', content)
        if len(re.sub(r'\s', '', prose)) < 200 or len(set(prose)) < 20:
            raise ProbeFailure('content_empty', 'Chapter text is empty or too short')
        if re.search(r'请(?:先)?登录后(?:阅读|查看)|购买本章|章节内容不存在|暂无正文', content):
            raise ProbeFailure('content_locked', 'Chapter text is not accessible')
        pages[-1]['text'] = content
        audit = self.policy.audit_source(source, pages)
        status = {'pass': 'valid', 'block': 'blocked', 'review': 'review'}[audit['decision']]
        return {'status': status, 'kind': 'reading', 'audit': audit,
                'book': {'name': book_name, 'url': book_url, 'chapter_url': chapter_url}, 'content_length': len(content)}
