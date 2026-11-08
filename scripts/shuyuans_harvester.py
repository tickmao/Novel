#!/usr/bin/env python3
"""把 yckceo 的「书源合集」并入原始池。

合集是人工整理过的成批书源（体积远大于单条书源目录），是本项目扩大可验证
源池的主要入口。集成方式与 `/yuedu/shuyuan`（单条书源）一致：只做目录发现与
下载入库，不作任何准入判断——准入仍由静态审核与运行期实读证据决定。

合集更新时间以站点上的相对时间（`3小时前` / `10/24 12:14` / `2023/04/18`）
呈现，因此变更判定用「日期桶 + 源数量」而不是原始标签：相对标签每天都会变，
按标签比对会导致每轮重复下载十几 MB。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from reading_validator import ProbeFailure
from source_store import parse_time, utcnow

LISTING_BLOCK = re.compile(r'<div class="ylist"[^>]*>(.*?)</div>\s*</div>', re.S)
LISTING_LINK = re.compile(r'href="/yuedu/shuyuans/content/id/(\d+)\.html"[^>]*>(.*?)</a>', re.S)
LISTING_TIME = re.compile(r'<p class="m-right"[^>]*>([^<]+)</p>')
LISTING_SIZE = re.compile(r'源数量:(\d+)')
LISTING_PAGES = re.compile(r'/yuedu/shuyuans/index\.html\?page=(\d+)')
TAG = re.compile(r'<[^>]+>')
RELATIVE = re.compile(r'(\d+)\s*(分钟|小时|天|周|月)前')
CURRENT_YEAR = re.compile(r'(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})')
FULL_DATE = re.compile(r'(\d{4})/(\d{1,2})/(\d{1,2})')
UNITS = {'分钟': 'minutes', '小时': 'hours', '天': 'days', '周': 'weeks', '月': 'days'}


@dataclass
class ShuyuansListing:
    collection_id: str
    title: str
    updated_label: str
    updated_at: str = ''
    size: int = 0


def parse_updated_at(label, now=None):
    """Return the moment a collection was last updated, or ``None``.

    The site mixes relative labels, bare month/day values and full dates; a bare
    month/day in January can also belong to the previous year.
    """
    text = str(label or '').strip()
    now = now or datetime.now(timezone.utc)
    match = RELATIVE.fullmatch(text)
    if match:
        amount, unit = int(match[1]), UNITS[match[2]]
        if match[2] == '月':
            return now - timedelta(days=amount * 30)
        return now - timedelta(**{unit: amount})
    match = CURRENT_YEAR.fullmatch(text)
    if match:
        month, day, hour, minute = (int(value) for value in match.groups())
        try:
            stamp = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None
        if stamp > now + timedelta(days=1):
            stamp = stamp.replace(year=now.year - 1)
        return stamp
    match = FULL_DATE.fullmatch(text)
    if match:
        year, month, day = (int(value) for value in match.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def parse_listing_page(value, now=None):
    """Return the collections on one listing page plus the last page number."""
    listings, seen = [], set()
    for block in LISTING_BLOCK.findall(value):
        link = LISTING_LINK.search(block)
        if not link or link[1] in seen:
            continue
        seen.add(link[1])
        label = LISTING_TIME.search(block)
        size = LISTING_SIZE.search(block)
        stamp = parse_updated_at(label[1], now) if label else None
        listings.append(ShuyuansListing(
            collection_id=link[1],
            title=' '.join(TAG.sub('', link[2]).split()),
            updated_label=(label[1].strip() if label else ''),
            updated_at=stamp.isoformat() if stamp else '',
            size=int(size[1]) if size else 0,
        ))
    # Some layouts list the collections as plain anchors without the block wrapper.
    if not listings:
        for key, title in re.findall(r'href="/yuedu/shuyuans/content/id/(\d+)\.html"[^>]*>(.*?)</a>', value, re.S):
            if key in seen:
                continue
            seen.add(key)
            listings.append(ShuyuansListing(key, ' '.join(TAG.sub('', title).split()), ''))
    pages = [int(page) for page in LISTING_PAGES.findall(value)]
    return listings, max(pages, default=1)


def parse_collection(value):
    """Accept every payload shape the site has used for a collection file."""
    payload = value
    if isinstance(payload, dict):
        payload = next((payload[key] for key in ('data', 'sources', 'bookSources', 'list')
                        if isinstance(payload.get(key), list)), [payload])
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and str(item.get('bookSourceUrl') or '').strip()]


def change_signature(listing):
    """Identity of one collection revision at day granularity."""
    day = listing.updated_at[:10]
    return [day, listing.size]


class ShuyuansHarvester:
    def __init__(self, policy=None, *, base_url='https://www.yckceo.com',
                 list_path='/yuedu/shuyuans/index.html',
                 json_path_template='/yuedu/shuyuans/json/id/{id}.json',
                 request_delay=2.0, max_age_days=365, min_sources=5,
                 refetch_days=7, max_tracked=1200, max_new_sources=2000, **_):
        self.policy = policy
        self.base_url = base_url.rstrip('/')
        self.list_path, self.json_path_template = list_path, json_path_template
        self.request_delay = max(1.0, float(request_delay))
        self.max_age_days = int(max_age_days)
        self.min_sources = int(min_sources)
        self.refetch_days = int(refetch_days)
        self.max_tracked = int(max_tracked)
        self.max_new_sources = int(max_new_sources)

    def listing_url(self, page=1):
        url = urljoin(self.base_url, self.list_path)
        return url if page == 1 else f'{url}?page={page}'

    def json_url(self, collection_id):
        return urljoin(self.base_url, self.json_path_template.format(id=collection_id))

    async def sync(self, store, client, *, deadline=None, save=True, scan=True, fetch_cap=6):
        state = store.state.setdefault('providers', {}).setdefault(
            'shuyuans', {'catalog': {}, 'scan_cursor': 1})
        now = datetime.now(timezone.utc)
        cooldown = parse_time(state.get('cooldown_until'))
        if cooldown and cooldown > now:
            return {'skipped': 'rate_limit', 'retry_at': cooldown.isoformat(),
                    'pending_count': state.get('pending_count', 0)}
        catalog = state['catalog']
        cutoff = now - timedelta(days=self.max_age_days)
        deadline = deadline or time.monotonic() + 600
        report = {'listed': 0, 'fetched_ids': 0, 'sources': 0, 'new_versions': 0,
                  'failed': 0, 'pages': 0, 'stale_skipped': 0}
        scan_deadline = time.monotonic() + max(0, deadline - time.monotonic()) / 2
        page = state.get('scan_cursor', 1) if scan else 1
        last_page = state.get('last_page', 1)
        while scan and time.monotonic() < scan_deadline:
            try:
                response = await client.fetch({'url': self.listing_url(page)})
                listings, last_page = parse_listing_page(response.text, now)
                if not listings:
                    raise ValueError('Collection listing page has no entries')
            except Exception as exc:
                report['catalog_error'] = str(exc)[:150]
                if isinstance(exc, ProbeFailure) and exc.kind == 'rate_limit':
                    state['cooldown_until'] = (now + timedelta(seconds=max(3600, exc.retry_after))).isoformat()
                break
            for listing in listings:
                if listing.updated_at and parse_time(listing.updated_at) < cutoff:
                    report['stale_skipped'] += 1
                    continue
                if listing.size and listing.size < self.min_sources:
                    report['stale_skipped'] += 1
                    continue
                if listing.collection_id not in catalog and len(catalog) >= self.max_tracked:
                    report['catalog_capped'] = True
                    continue
                entry = catalog.setdefault(listing.collection_id, {'attempts': 0})
                signature = change_signature(listing)
                if entry.get('signature') != signature:
                    entry['next_fetch_at'] = utcnow()
                entry.update(title=listing.title, signature=signature, updated_at=listing.updated_at,
                             updated_label=listing.updated_label, claimed_size=listing.size,
                             last_seen_at=utcnow())
                report['listed'] += 1
            report['pages'] += 1
            state['last_page'] = last_page
            if page >= last_page:
                state['last_full_scan_at'] = utcnow()
                state['scan_cursor'] = 1
                break
            page += 1
            state['scan_cursor'] = page
            await asyncio.sleep(self.request_delay)
            if save and report['pages'] % 4 == 0:
                store.save()

        # Newly registered collections are due immediately, so the check reads a
        # fresh clock instead of the one captured before the scan.
        due = datetime.now(timezone.utc)
        pending = []
        for key, entry in catalog.items():
            next_fetch = parse_time(entry.get('next_fetch_at'))
            if next_fetch and next_fetch > due:
                continue
            pending.append((key, entry))
        pending.sort(key=lambda item: (item[1].get('updated_at') or '',
                                       -int(item[1].get('claimed_size') or 0)), reverse=True)
        for key, entry in pending[:max(1, int(fetch_cap))]:
            if time.monotonic() >= deadline:
                break
            # A collection carries hundreds of sources and the probe budget cannot read
            # them any faster. The cap counts new revisions rather than new records: a
            # collection of sources already in the store adds no records but still adds
            # queue work, and the queue is what the next run has to pay for. One
            # collection is always taken: progress beats a perfect bound.
            if (self.max_new_sources and report['fetched_ids']
                    and report['new_versions'] >= self.max_new_sources):
                report['deferred_collections'] = len(pending) - report['fetched_ids']
                break
            await asyncio.sleep(self.request_delay)
            report['fetched_ids'] += 1
            try:
                response = await client.fetch({'url': self.json_url(key),
                                               'headers': {'Referer': self.base_url + '/'}})
                sources = parse_collection(json.loads(response.text.lstrip('\ufeff')))
                if not sources:
                    raise ValueError('Collection payload has no book sources')
                for source in sources:
                    key2, revision = store.ingest(source, {'provider': 'shuyuans', 'item_id': key,
                                                           'title': entry.get('title', ''),
                                                           'url': self.json_url(key)})
                    version = store.get(key2)['versions'][revision]
                    if version['first_seen_at'] == version['last_seen_at']:
                        report['new_versions'] += 1
                entry.update(last_fetched_at=utcnow(), attempts=0, error=None, size=len(sources),
                             next_fetch_at=(now + timedelta(days=self.refetch_days)).isoformat())
                report['sources'] += len(sources)
            except Exception as exc:
                entry['attempts'] = entry.get('attempts', 0) + 1
                delay = (1, 6, 24)[min(entry['attempts'] - 1, 2)]
                entry['next_fetch_at'] = (now + timedelta(hours=delay)).isoformat()
                entry['error'] = str(exc)[:150]
                report['failed'] += 1
                if isinstance(exc, ProbeFailure) and exc.kind == 'rate_limit':
                    state['cooldown_until'] = (now + timedelta(seconds=max(3600, exc.retry_after))).isoformat()
                    entry['next_fetch_at'] = state['cooldown_until']
                    break
            # One collection marks about every shard dirty, so a per-collection save
            # would rewrite hundreds of megabytes. The caller saves once per run and
            # still does so when the deadline cancels this task.
            print(f'SHUYUANS: fetched {report["fetched_ids"]}, archived {report["sources"]},'
                  f' failed {report["failed"]}', flush=True)
        state['last_run_at'] = utcnow()
        # A collection registered a moment ago is already waiting, so the count uses
        # the same fresh clock the queue used.
        settled = datetime.now(timezone.utc)
        state['pending_count'] = sum(1 for entry in catalog.values()
                                     if not parse_time(entry.get('next_fetch_at'))
                                     or parse_time(entry['next_fetch_at']) <= settled)
        report.update(catalog_count=len(catalog), pending_count=state['pending_count'])
        if save:
            store.save()
        return report
