"""Refuse to write state when this runner cannot reach the public internet."""

from __future__ import annotations

import asyncio
import json
import sys

ENDPOINTS = ('https://api.github.com/', 'https://cdn.jsdelivr.net/', 'https://www.baidu.com/')


async def _fetch(url, timeout):
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.head(url, allow_redirects=True) as response:
            return response.status


async def check(endpoints=ENDPOINTS, timeout=10, fetch=None):
    fetch = fetch or _fetch
    results = []
    for url in endpoints:
        try:
            status = await fetch(url, timeout)
        except Exception as exc:  # noqa: BLE001 - any transport error means unreachable
            results.append({'url': url, 'status': None, 'error': type(exc).__name__})
            continue
        results.append({'url': url, 'status': status})
    reachable = [item for item in results if item['status'] and item['status'] < 500]
    return {'ok': bool(reachable), 'reachable': len(reachable), 'results': results}


async def run(urls=None):
    return await check(urls or ENDPOINTS)


def main():
    urls = tuple(sys.argv[1:]) or ENDPOINTS
    result = asyncio.run(run(urls))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
