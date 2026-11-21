"""Durable catalog discovery and version-aware YCKCEO downloads."""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from reading_validator import ProbeFailure
from source_store import digest, parse_time, utcnow


@dataclass
class YckceoListing:
    source_id: str
    title: str
    updated_label: str = ''


def parse_listing_page(value: str):
    from lxml import html as markup
    tree = markup.fromstring(value)
    listings, seen = [], set()
    for node in tree.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " ylist ")]'):
        links = node.xpath('.//h2/a[contains(@href,"/content/id/")]')
        if not links:
            continue
        match = re.search(r'/content/id/(\d+)\.html', links[0].get('href', ''))
        if not match or match[1] in seen:
            continue
        seen.add(match[1])
        updated = node.xpath('.//p/text()')
        listings.append(YckceoListing(match[1], ' '.join(links[0].text_content().split()), ' '.join(updated).strip()))
    # Accept minimal fixture pages and older layouts.
    if not listings:
        for key, title in re.findall(r'href=["\']/yuedu/shuyuan/content/id/(\d+)\.html["\'][^>]*>(.*?)</a>', value, re.S):
            if key not in seen:
                listings.append(YckceoListing(key, html.unescape(re.sub('<[^>]+>', '', title)).strip()))
                seen.add(key)
    pages = [int(page) for page in re.findall(r'/yuedu/shuyuan/index\.html\?page=(\d+)', value)]
    return listings, max(pages, default=1)


class YckceoHarvester:
    def __init__(self, policy=None, *, base_url='https://www.yckceo.com', list_path='/yuedu/shuyuan/index.html',
                 json_path_template='/yuedu/shuyuan/json/id/{id}.json', request_delay=1.0,
                 max_seen_ids=20000, **_):
        self.policy = policy
        self.base_url = base_url.rstrip('/')
        self.list_path, self.json_path_template = list_path, json_path_template
        self.request_delay = max(1.0, request_delay)
        # The catalogue grows forever otherwise, and a catalogue nobody reads is
        # pure storage. Capping it is reported, never silent.
        self.max_ids = int(max_seen_ids or 0)

    def listing_url(self, page=1):
        url = urljoin(self.base_url, self.list_path)
        return url if page == 1 else f'{url}?page={page}'

    def json_url(self, key):
        return urljoin(self.base_url, self.json_path_template.format(id=key))

    def parse_source_payload(self, payload):
        if isinstance(payload, dict):
            payload = payload.get('data', payload.get('sources', [payload]))
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    async def sync(self, store, client, *, full_scan=False, fetch_cap=150, daily_pages=5, deadline=None, save=True, scan=True):
        state = store.state.setdefault('providers', {}).setdefault('yckceo', {'catalog': {}, 'scan_cursor': 1})
        report = {'listed': 0, 'fetched_ids': 0, 'sources': 0, 'failed': 0, 'pages': 0}
        report.update({key: state.get(key, default) for key, default in (
            ('scan_cursor', 1), ('last_page', 0), ('catalog_failed_page', None),
            ('catalog_failure_attempts', 0), ('stalled_scans', 0))})
        cooldown = parse_time(state.get('cooldown_until'))
        if cooldown and cooldown > datetime.now(timezone.utc):
            return {**report, 'skipped': 'rate_limit', 'retry_at': cooldown.isoformat(),
                    'pending_count': state.get('pending_count', 0)}
        catalog = state['catalog']
        deadline = deadline or time.monotonic() + 3600
        # Leave half of each run for downloads while a large catalog scan resumes.
        scan_deadline = time.monotonic() + max(0, deadline - time.monotonic()) / 2
        page = state.get('scan_cursor', 1) if full_scan else 1
        last_page = state.get('last_page', 0)
        progressed = completed = False
        while scan and time.monotonic() < scan_deadline and (full_scan or page <= daily_pages):
            # 只有持久化的页数才可用于校验恢复位置；新目录仍须请求第一页。
            if full_scan and last_page > 0 and page > last_page:
                report.update(catalog_error='Catalog cursor exceeds last page', catalog_error_page=page)
                state.update(scan_cursor=1, scan_incomplete=True, stalled_scans=0)
                progressed = True
                page = 1
            try:
                response = await client.fetch({'url': self.listing_url(page)})
                listings, bound = parse_listing_page(response.text)
            except Exception as exc:
                report.update(catalog_error=(str(exc) or type(exc).__name__)[:150], catalog_error_page=page)
                if isinstance(exc, ProbeFailure) and exc.kind == 'rate_limit':
                    state['cooldown_until'] = (datetime.now(timezone.utc) + timedelta(seconds=max(3600, exc.retry_after))).isoformat()
                    break
                attempts = state.get('catalog_failure_attempts', 0) if state.get('catalog_failed_page') == page else 0
                state.update(catalog_failed_page=page, catalog_failure_attempts=min(3, attempts + 1))
                if state['catalog_failure_attempts'] < 3:
                    break
                report['skipped_page'] = page
                state.update(catalog_failed_page=None, catalog_failure_attempts=0)
                at_end = last_page > 0 and page >= last_page
                page = 1 if at_end else page + 1
                if full_scan:
                    state.update(scan_cursor=page, scan_incomplete=not at_end, stalled_scans=0)
                    progressed = True
                if at_end:
                    break
                await asyncio.sleep(self.request_delay)
                continue
            if not listings:
                report.update(catalog_error='Catalog page has no source entries', catalog_error_page=page)
                if full_scan:
                    progressed = progressed or state.get('scan_cursor', 1) != 1
                    state.update(scan_cursor=1, scan_incomplete=False)
                break
            last_page = bound
            if state.get('catalog_failed_page') == page:
                state.update(catalog_failed_page=None, catalog_failure_attempts=0)
            now = utcnow()
            for listing in listings:
                if listing.source_id not in catalog and self.max_ids and len(catalog) >= self.max_ids:
                    report['catalog_capped'] = True
                    continue
                entry = catalog.setdefault(listing.source_id, {'attempts': 0})
                signature = digest([listing.title, listing.updated_label])
                if entry.get('signature') != signature:
                    entry['next_fetch_at'] = now
                entry.update(title=listing.title, signature=signature, last_seen_at=now)
                # Recent entries are revisited daily even if their ID is unchanged.
                fetched = parse_time(entry.get('last_fetched_at'))
                if not full_scan and fetched and datetime.now(timezone.utc) - fetched >= timedelta(days=1):
                    entry['next_fetch_at'] = now
            report['listed'] += len(listings)
            report['pages'] += 1
            state['last_page'] = last_page
            if full_scan:
                state['scan_cursor'] = page + 1 if page < last_page else 1
                progressed = progressed or state['scan_cursor'] != page
                state['stalled_scans'] = 0
                if page >= last_page:
                    if not state.get('scan_incomplete', False):
                        state['last_full_scan_at'] = now
                        completed = True
                    state['scan_incomplete'] = False
            elif page >= last_page or page >= daily_pages:
                completed = True
            if save and report['pages'] % 5 == 0:
                store.save()
            if page >= last_page:
                break
            page += 1
            await asyncio.sleep(self.request_delay)

        if progressed or completed:
            state['stalled_scans'] = 0
        elif report.get('catalog_error'):
            state['stalled_scans'] = state.get('stalled_scans', 0) + 1
        report.update({key: state.get(key, report[key]) for key in (
            'scan_cursor', 'last_page', 'catalog_failed_page', 'catalog_failure_attempts', 'stalled_scans')})

        now = datetime.now(timezone.utc)
        pending = [(key, entry) for key, entry in catalog.items()
                   if not parse_time(entry.get('next_fetch_at')) or parse_time(entry['next_fetch_at']) <= now]
        pending.sort(key=lambda item: (bool(item[1].get('last_fetched_at')), item[1].get('next_fetch_at', ''), -int(item[0])))
        cooldown = parse_time(state.get('cooldown_until'))
        if cooldown and cooldown > now:
            pending = []
        for key, entry in pending[:fetch_cap]:
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(self.request_delay)
            report['fetched_ids'] += 1
            try:
                response = await client.fetch({'url': self.json_url(key), 'headers': {'Referer': self.base_url + '/'}})
                sources = self.parse_source_payload(json.loads(response.text))
                if not sources:
                    raise ValueError('Empty source payload')
                for source in sources:
                    store.ingest(source, {'provider': 'yckceo', 'item_id': key, 'title': entry['title'], 'url': self.json_url(key)})
                entry.update(last_fetched_at=utcnow(), attempts=0, error=None,
                             next_fetch_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat())
                report['sources'] += len(sources)
            except Exception as exc:
                entry['attempts'] = entry.get('attempts', 0) + 1
                delay = (1, 6, 24)[min(entry['attempts'] - 1, 2)]
                entry['next_fetch_at'] = (datetime.now(timezone.utc) + timedelta(hours=delay)).isoformat()
                entry['error'] = str(exc)[:150]
                report['failed'] += 1
                if isinstance(exc, ProbeFailure) and exc.kind == 'rate_limit':
                    state['cooldown_until'] = (datetime.now(timezone.utc) + timedelta(seconds=max(3600, exc.retry_after))).isoformat()
                    entry['next_fetch_at'] = state['cooldown_until']
                    break
            if save and report['fetched_ids'] % 25 == 0:
                store.save()
            if report['fetched_ids'] % 25 == 0:
                print(f'YCKCEO: fetched {report["fetched_ids"]}, archived {report["sources"]}, failed {report["failed"]}', flush=True)
        state['last_run_at'] = utcnow()
        state['pending_count'] = sum(not item.get('last_fetched_at') for item in catalog.values())
        report.update(catalog_count=len(catalog), pending_count=state['pending_count'])
        if save:
            store.save()
        return report
