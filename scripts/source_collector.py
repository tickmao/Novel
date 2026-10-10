"""Collect additional public feeds into the versioned raw store."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin

from reading_validator import HTTPClient
from source_store import SourceStore, read_json, digest, utcnow, parse_time
from reading_validator import ProbeFailure


def parse_sources(value):
    if isinstance(value, dict):
        if 'bookSourceUrl' in value:
            value = [value]
        else:
            value = next((value[key] for key in ('data', 'sources', 'bookSources', 'list') if isinstance(value.get(key), list)), [])
    return [item for item in value if isinstance(item, dict) and item.get('bookSourceUrl')] if isinstance(value, list) else []


async def collect_into(store, client, config, *, deadline=None, save=True):
    report = {'sources': 0, 'new_versions': 0, 'files': 0, 'unchanged': 0, 'errors': [], 'channels': {}}
    deadline = deadline or time.monotonic() + 600
    states = store.state.setdefault('external_feeds', {})

    async def ingest_url(url, provider, channel):
        state = states.setdefault(url, {})
        next_fetch = parse_time(state.get('next_fetch_at'))
        if next_fetch and next_fetch > datetime.now(timezone.utc):
            return
        stats = report['channels'].setdefault(channel, {'sources': 0, 'new_versions': 0})
        headers = {}
        if state.get('etag'):
            headers['If-None-Match'] = state['etag']
        if state.get('last_modified'):
            headers['If-Modified-Since'] = state['last_modified']
        try:
            if time.monotonic() >= deadline:
                return
            response = await client.fetch({'url': url, 'headers': headers})
            state['last_attempt_at'] = utcnow()
            checksum = digest(response.text)
            if response.status == 304 or state.get('sha256') == checksum:
                report['unchanged'] += 1
            else:
                sources = parse_sources(json.loads(response.text.lstrip('\ufeff')))
                if not sources:
                    raise ValueError('Feed contains no source definitions')
                for item in sources:
                    key, revision = store.ingest(item, {'provider': provider, 'channel': channel, 'url': url})
                    if store.get(key)['versions'][revision]['first_seen_at'] == store.get(key)['versions'][revision]['last_seen_at']:
                        report['new_versions'] += 1
                        stats['new_versions'] += 1
                report['sources'] += len(sources)
                stats['sources'] += len(sources)
                state['sha256'] = checksum
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            state.update(etag=response_headers.get('etag'), last_modified=response_headers.get('last-modified'),
                         last_success_at=utcnow(), failures=0,
                         next_fetch_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
            report['files'] += 1
        except (ValueError, OSError, ProbeFailure) as exc:
            state['failures'] = state.get('failures', 0) + 1
            hours = (1, 6, 24)[min(state['failures'] - 1, 2)]
            delay = max(hours * 3600, getattr(exc, 'retry_after', 0))
            state['next_fetch_at'] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            report['errors'].append({'provider': channel, 'error': str(exc)[:150]})
        if save:
            store.save()

    for repo in config.get('github_repos', []):
        if time.monotonic() >= deadline:
            break
        name = repo['name']
        try:
            limit = int(repo.get('max_files', 3))
            patterns = repo.get('file_patterns', [])
            files, queue, visited = [], [(path, 0) for path in repo.get('files', [''])], 0
            while queue and len(files) < limit and visited < 12 and time.monotonic() < deadline:
                path, depth = queue.pop(0)
                listing = await client.fetch({'url': f'https://api.github.com/repos/{name}/contents/{quote(path, safe="/")}'})
                entries = json.loads(listing.text)
                entries = entries if isinstance(entries, list) else [entries]
                visited += 1
                for item in entries:
                    if item.get('type') == 'file' and item.get('download_url') and (
                            item['name'].endswith('.json') or any(word.lower() in item['name'].lower() for word in patterns)):
                        files.append(item)
                    elif item.get('type') == 'dir' and depth < int(repo.get('max_depth', 2)):
                        queue.append((item['path'], depth + 1))
            for item in files[:limit]:
                await ingest_url(item['download_url'], 'github', name)
        except Exception as exc:
            report['errors'].append({'provider': name, 'error': str(exc)[:150]})
    for site in config.get('source_websites', []):
        if time.monotonic() >= deadline:
            break
        if 'yckceo.com' in site['url']:
            continue
        try:
            if site.get('mode') == 'json' or site['url'].split('?', 1)[0].endswith('.json'):
                await ingest_url(site['url'], 'website', site['url'])
                continue
            response = await client.fetch({'url': site['url']})
            links = list(dict.fromkeys(urljoin(response.url, link) for link in re.findall(r'''href=["']([^"']+\.json(?:\?[^"']*)?)["']''', response.text)))
            for link in links[:int(site.get('max_links', 3))]:
                await ingest_url(link, 'website', site['url'])
        except Exception as exc:
            report['errors'].append({'provider': site['url'], 'error': str(exc)[:150]})
    if save:
        store.save()
    return report


class SourceCollector:
    def __init__(self, timeout=30):
        self.timeout = timeout

    async def collect_all_sources(self, large_files=None):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = SourceStore(Path(tmp))
            config = read_json(Path(__file__).resolve().parents[1] / 'config/source_channels.json', {})
            async with HTTPClient(timeout=self.timeout) as client:
                await collect_into(store, client, config)
            result = [version['source'] for item in store.records() for version in item['versions'].values()]
            for path in large_files or []:
                result.extend(parse_sources(read_json(Path(path), [])))
            return result


async def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--large-files', type=Path, nargs='*')
    args = parser.parse_args()
    values = await SourceCollector().collect_all_sources(args.large_files)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(values, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    asyncio.run(main())
