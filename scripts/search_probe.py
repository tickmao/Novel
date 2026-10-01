#!/usr/bin/env python3
"""
搜索探测验证
- 解析 Legado searchUrl（{{key}}/{{page}}、`,{json}` 选项、相对路径）
- 用热门书名实际发起搜索，响应包含书名才算可用
- 识别 Cloudflare/验证码/登录拦截页
- JS 型 searchUrl 无法执行，降级为首页 GET 探测
"""

import asyncio
import json
import re
from typing import Dict, Optional, Tuple
from urllib.parse import quote, urljoin

try:
    import aiohttp
except ImportError:  # pragma: no cover - optional at import time
    aiohttp = None

PROBE_KEYWORDS = ('斗罗大陆', '凡人修仙传')
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
JS_MARKERS = ('@js:', '<js>', 'java.', '{{java', 'source.')
BLOCK_PATTERNS = re.compile(
    r'cf-chl|cf_chl|Just a moment|challenge-platform|Attention Required|'
    r'请输入验证码|人机验证|domain is for sale|域名出售',
    re.I,
)
TEMPLATE_RE = re.compile(r'\{\{\s*(key|page|searchKey|searchPage)\s*\}\}')

LEVEL_SEARCH = 'search'
LEVEL_HOME = 'home'


def is_js_search(search_url: str) -> bool:
    return any(marker in search_url for marker in JS_MARKERS)


def build_search_request(source: Dict, keyword: str) -> Optional[Dict]:
    """
    把 Legado searchUrl 转成请求描述；无法静态解析时返回 None。

    Returns:
        {'method', 'url', 'body', 'headers', 'charset'}
    """
    search_url = str(source.get('searchUrl') or '').strip()
    base_url = str(source.get('bookSourceUrl') or '').split('#')[0].strip()
    if not search_url or not base_url or is_js_search(search_url):
        return None

    # 规则中其他 {{...}} 表达式无法静态求值
    stripped = TEMPLATE_RE.sub('', search_url)
    if '{{' in stripped:
        return None

    url_part, options = search_url, {}
    options_match = re.search(r',\s*\{', search_url)
    split_at = options_match.start() if options_match else -1
    if split_at != -1:
        url_part = search_url[:split_at]
        try:
            options = json.loads(search_url[split_at + 1:])
        except (json.JSONDecodeError, ValueError):
            try:
                options = json.loads(search_url[split_at + 1:].replace("'", '"'))
            except (json.JSONDecodeError, ValueError):
                return None
        if not isinstance(options, dict):
            return None

    charset = str(options.get('charset') or 'utf-8').lower()
    try:
        encoded = quote(keyword.encode(charset))
    except (LookupError, UnicodeEncodeError):
        encoded = quote(keyword)

    def fill(text: str) -> str:
        return TEMPLATE_RE.sub(
            lambda m: encoded if m.group(1) in ('key', 'searchKey') else '1',
            text,
        )

    if any(key not in {'method', 'body', 'headers', 'charset'} for key in options):
        return None
    method = str(options.get('method') or 'GET').upper()
    if method not in ('GET', 'POST'):
        return None
    body = options.get('body')
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    body = fill(str(body)) if body is not None else None
    headers = options.get('headers') if isinstance(options.get('headers'), dict) else {}
    header_value = source.get('header') or ''
    header_text = json.dumps(header_value) if isinstance(header_value, dict) else str(header_value).strip()
    if is_js_search(header_text):
        return None
    if header_text and not is_js_search(header_text):
        try:
            extra = json.loads(header_text)
            if isinstance(extra, dict):
                headers = {**extra, **headers}
        except (json.JSONDecodeError, ValueError):
            return None

    return {
        'method': method,
        'url': urljoin(base_url.rstrip('/') + '/', fill(url_part.strip())),
        'body': body,
        'headers': {str(k): str(v) for k, v in headers.items()},
        'charset': charset,
    }


def is_blocked_page(text: str) -> bool:
    return bool(BLOCK_PATTERNS.search(text[:20000]))


async def _fetch(session, request: Dict, timeout: int) -> Tuple[int, str]:
    headers = {'User-Agent': USER_AGENT, **request.get('headers', {})}
    data = request.get('body')
    if data is not None and request['method'] == 'POST' and 'Content-Type' not in headers:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    async with session.request(
        request['method'],
        request['url'],
        data=data.encode(request['charset'], errors='ignore') if data else None,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
        allow_redirects=True,
    ) as resp:
        raw = await resp.read()
        charset = resp.charset or request.get('charset') or 'utf-8'
        try:
            text = raw.decode(charset, errors='ignore')
        except LookupError:
            text = raw.decode('utf-8', errors='ignore')
        return resp.status, text


async def probe_source(session, source: Dict, timeout: int = 10) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    探测单个书源。

    Returns:
        (是否可用, 验证级别 search/home, 错误信息)
    """
    if not source.get('bookSourceUrl'):
        return False, None, 'URL 为空'

    first_request = build_search_request(source, PROBE_KEYWORDS[0])
    if first_request is None:
        return await _probe_home(session, source, timeout)

    last_error = '搜索结果不含关键词'
    for keyword in PROBE_KEYWORDS:
        request = build_search_request(source, keyword)
        try:
            status, text = await _fetch(session, request, timeout)
        except asyncio.TimeoutError:
            return False, LEVEL_SEARCH, '搜索超时'
        except Exception as e:
            return False, LEVEL_SEARCH, str(e)[:50] or type(e).__name__
        if status >= 400:
            return False, LEVEL_SEARCH, f'HTTP {status}'
        if is_blocked_page(text):
            return False, LEVEL_SEARCH, '拦截/验证页'
        if keyword in text or quote(keyword) in text or json.dumps(keyword)[1:-1] in text:
            return True, LEVEL_SEARCH, None
    return False, LEVEL_SEARCH, last_error


async def _probe_home(session, source: Dict, timeout: int) -> Tuple[bool, Optional[str], Optional[str]]:
    request = {
        'method': 'GET',
        'url': str(source.get('bookSourceUrl')).split('#')[0].strip(),
        'body': None,
        'headers': {},
        'charset': 'utf-8',
    }
    try:
        status, text = await _fetch(session, request, timeout)
    except asyncio.TimeoutError:
        return False, LEVEL_HOME, '超时'
    except Exception as e:
        return False, LEVEL_HOME, str(e)[:50] or type(e).__name__
    if status >= 400:
        return False, LEVEL_HOME, f'HTTP {status}'
    if is_blocked_page(text):
        return False, LEVEL_HOME, '拦截/验证页'
    if len(text.strip()) < 200:
        return False, LEVEL_HOME, '页面内容过少'
    return True, LEVEL_HOME, None
