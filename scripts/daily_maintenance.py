"""Run resumable collection, validation, and publication under one writer lock."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reading_validator import HTTPClient
from runtime_validator import RuntimeReadingValidator as ReadingValidator
from runtime_config import ENVIRONMENT_FAILURES
from maintenance_lock import writer_lock
from source_collector import collect_into
from source_inventory import SourceInventory
from source_store import atomic_bundle, digest, json_bytes, parse_time, read_json, utcnow
from maintenance_acceptance import acceptance_status
from yckceo_harvester import YckceoHarvester
from submissions import collect_submissions


class DailyMaintenance:
    def __init__(self, base_dir=None):
        self.inventory = SourceInventory(base_dir)
        self.base_dir = self.inventory.base_dir
        self.metadata_file = self.inventory.metadata_file

    async def validate(self, validator, refs, *, mode, deadline, save, report):
        inventory, store = self.inventory, self.inventory.store
        known = {ref for ref in refs if store.get(ref[0])['versions'][ref[1]].get('validation', {}).get('last_deep_success_at')}
        baseline, completed = {}, []
        counts, kinds, examples = Counter(), Counter(), {}
        for offset in range(0, len(refs), 20):
            if time.monotonic() >= deadline:
                report['budget_exhausted'] = True
                break
            batch = refs[offset:offset + 20]
            for key, revision in batch:
                version = store.get(key)['versions'][revision]
                baseline[key, revision] = (deepcopy(version.get('validation', {})), deepcopy(version.get('audit', {})))
            tasks = [asyncio.create_task(validator.probe(inventory.audit_payload(store.get(key)['versions'][revision]), mode))
                     for key, revision in batch]
            _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - time.monotonic()))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if pending:
                report['budget_exhausted'] = True
            pairs = [(ref, task.result()) for ref, task in zip(batch, tasks) if task not in pending]
            batch, results = [p[0] for p in pairs], [p[1] for p in pairs]
            for ref, result in zip(batch, results):
                completed.append((ref, result))
                counts[result['status']] += 1
                kinds[result['kind']] += 1
                if result['status'] != 'valid' and result['kind'] not in examples:
                    examples[result['kind']] = {'name': store.get(ref[0])['versions'][ref[1]]['source'].get('bookSourceName'),
                                               'error': result.get('error'), 'status': result['status']}
                inventory.record_result(*ref, result)
            if results and all(result['kind'] in ENVIRONMENT_FAILURES for result in results):
                report['engine_incident'] = True
                if save:
                    store.save()
                break
            prior = [(ref, result) for ref, result in completed if ref in known]
            sites = {inventory.site(store.get(ref[0])['versions'][ref[1]]['source']) for ref, _ in prior}
            network_failures = sum(result['kind'] in ('network', 'timeout') for _, result in prior)
            if len(prior) >= 20 and len(sites) >= 5 and network_failures / len(prior) >= 0.5:
                report['network_incident'] = True
                for (key, revision), result in completed:
                    version = store.get(key)['versions'][revision]
                    if (result['kind'] in ('network', 'timeout')
                            and result.get('audit', {}).get('decision') not in ('block', 'review')):
                        version['validation'], version['audit'] = baseline[key, revision]
                    store.touch(key)
                if save:
                    store.save()
                break
            if save:
                store.save()
            print(f'{mode}: {len(completed)}/{len(refs)} {dict(counts)}', flush=True)
        report[mode] = dict(counts)
        report[mode + '_kinds'] = dict(kinds)
        report[mode + '_examples'] = examples
        report[mode + '_checked'] = len(completed)

    async def maintain(self, batch_size=300, concurrency=20, timeout=10, dry_run=False,
                       mode='daily', collect=True, publish=False, max_minutes=90, fetch_cap=None, backfill_rounds=1):
        with writer_lock(self.base_dir):
            inventory = self.inventory
            inventory.store = __import__('source_store').SourceStore(inventory.store.root)
            store = inventory.store
            if not store.exists:
                if dry_run:
                    raise RuntimeError('Run source_inventory.py migrate before a dry run')
                inventory.ensure_migrated()
            report = {'schema_version': 3, 'started_at': utcnow(), 'mode': mode, 'dry_run': dry_run,
                      'published': False, 'providers': {}}
            deadline = time.monotonic() + max_minutes * 60
            report['static_audit'] = inventory.audit_all()
            if not dry_run:
                store.save()
            _, _, initial = inventory.select()
            if mode == 'catchup' and not initial['needs_replenishment']:
                report['skipped'] = 'Inventory and catalog are complete'
                return report
            async with HTTPClient(concurrency, timeout, max_bytes=32 * 1024 * 1024) as client:
                if collect:
                    collection_deadline = (deadline if mode == 'backfill' else
                                           min(deadline, time.monotonic() + min(600, max_minutes * 60 * 0.15)))
                    report['providers']['submissions'] = await collect_submissions(
                        store, client, inventory.project_root, save=not dry_run, deadline=collection_deadline)
                    cfg = inventory.config.get('yckceo', {})
                    harvester = YckceoHarvester(inventory.policy, **cfg)
                    provider_state = store.state.get('providers', {}).get('yckceo', {})
                    last_scan = parse_time(provider_state.get('last_full_scan_at'))
                    full_scan = mode == 'backfill' or not last_scan or datetime.now(timezone.utc) - last_scan > timedelta(days=7)
                    batches = []
                    rounds = backfill_rounds if mode == 'backfill' else 1
                    for round_index in range(rounds):
                        result = await harvester.sync(
                            store, client, full_scan=full_scan, scan=round_index == 0,
                            daily_pages=int(cfg.get('daily_pages', 5)),
                            fetch_cap=fetch_cap if fetch_cap is not None else (300 if mode in ('backfill', 'catchup') else 150),
                            deadline=collection_deadline, save=not dry_run,
                        )
                        batches.append(result)
                        if time.monotonic() >= deadline or not result.get('fetched_ids') or not result.get('pending_count'):
                            break
                    report['providers']['yckceo'] = batches
                    last_other = parse_time(store.state.get('last_external_collection_at'))
                    if mode != 'backfill' and (not last_other or datetime.now(timezone.utc) - last_other > timedelta(days=1)):
                        channels = read_json(inventory.project_root / 'config/source_channels.json', {})
                        report['providers']['external'] = await collect_into(store, client, channels, deadline=collection_deadline, save=not dry_run)
                        if report['providers']['external']['files'] and not report['providers']['external']['errors']:
                            store.state['last_external_collection_at'] = utcnow()
                report['static_audit'] = inventory.audit_all()
                if mode != 'backfill':
                    validator = ReadingValidator(inventory.policy, concurrency=min(concurrency, 4))
                    refs = inventory.queue(limit=batch_size)
                    export, _, _ = inventory.select()
                    light_refs = [(item['_source_id'], item['_revision']) for item in export
                                  if (item['_source_id'], item['_revision']) not in set(refs)]
                    if mode == 'daily':
                        await self.validate(validator, light_refs, mode='light', deadline=deadline, save=not dry_run, report=report)
                    if not report.get('network_incident') and not report.get('engine_incident'):
                        await self.validate(validator, refs, mode='deep', deadline=deadline, save=not dry_run, report=report)
                    previous = read_json(inventory.base_dir / 'main/publication.json', {})
                    if (mode == 'daily' and previous.get('schema_version') == 3
                            and not report.get('network_incident') and not report.get('engine_incident')):
                        day = datetime.now(timezone.utc).date().isoformat()
                        selected = sorted(previous.get('sources', []), key=lambda item: digest([day, item['source_id']]))[:50]
                        spot_refs = [(item['source_id'], item['revision']) for item in selected]
                        await self.validate(validator, spot_refs, mode='spot', deadline=deadline, save=not dry_run, report=report)
            _, _, current = inventory.select()
            report['inventory'] = current
            report['publication_gate'] = inventory.publication_gate(current)
            report['finished_at'] = utcnow()
            store.state.setdefault('runs', []).append({key: value for key, value in report.items() if key != 'providers'})
            store.state['runs'] = store.state['runs'][-30:]
            if not dry_run:
                store.save()
                inventory.write_shadow()
                if publish and mode != 'backfill':
                    # A network incident can remove a confirmed adult source, but cannot certify new availability.
                    has_prior = (inventory.base_dir / 'main/publication.json').exists()
                    if not report['publication_gate']['ready']:
                        report['publication_skipped'] = 'Gate: ' + ', '.join(report['publication_gate']['missing'])
                    elif report.get('engine_incident'):
                        report['publication_skipped'] = 'Runtime incident; existing snapshot retained'
                    elif report.get('network_incident'):
                        prior = read_json(inventory.base_dir / 'main/publication.json', {})
                        allowed = {(item['source_id'], item['revision']) for item in prior.get('sources', [])}
                        safe, _, _ = inventory.select(allowed=allowed)
                        if has_prior and len(safe) < prior.get('count', 0):
                            inventory.publish(allowed=allowed, allow_empty=True)
                            report['published'] = True
                            report['safety_prune'] = True
                        else:
                            report['publication_skipped'] = 'Shared network failure; existing snapshot retained'
                    elif current['export_count'] or has_prior:
                        inventory.publish(allow_empty=has_prior)
                        report['published'] = True
                    else:
                        report['publication_skipped'] = 'Bootstrap has no verified sources yet'
                report['release_active'] = read_json(self.base_dir / 'main/publication.json', {}).get('schema_version') == 3
                store.state['runs'][-1] = {key: value for key, value in report.items() if key != 'providers'}
                report['acceptance'] = acceptance_status(store.state['runs'])
                store.save()
                atomic_bundle({self.base_dir / 'main' / 'maintenance_report.json': json_bytes(report)})
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            return report


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path)
    parser.add_argument('--mode', choices=['daily', 'catchup', 'backfill'], default='daily')
    parser.add_argument('--batch-size', type=int, default=300)
    parser.add_argument('--concurrency', type=int, default=20)
    parser.add_argument('--timeout', type=int, default=10)
    parser.add_argument('--max-minutes', type=float, default=90)
    parser.add_argument('--fetch-cap', type=int)
    parser.add_argument('--backfill-rounds', type=int, default=1)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-collect', action='store_true')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.batch_size < 0 or args.concurrency < 1 or args.timeout <= 0 or args.max_minutes <= 0 or args.backfill_rounds < 1 or (args.fetch_cap is not None and args.fetch_cap < 0):
        parser.error('Budgets and concurrency must be positive')
    if args.dry_run and args.publish:
        parser.error('--dry-run and --publish cannot be combined')
    maintenance = DailyMaintenance(args.base_dir)
    values = vars(args)
    values.pop('base_dir')
    values['collect'] = not values.pop('no_collect')
    report = await maintenance.maintain(**values)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write('published=' + str(bool(report.get('published'))).lower() + '\n')


if __name__ == '__main__':
    asyncio.run(main())
