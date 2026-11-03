"""Run resumable collection, validation, and publication under one writer lock."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reading_validator import HTTPClient, ProbeFailure
from runtime_validator import RuntimeReadingValidator as ReadingValidator
from runtime_config import ENVIRONMENT_FAILURES
from source_health import COOL_DOWN_HOURS, TRANSPORT_FAILURES
from maintenance_lock import writer_lock
from safe_updater import SafeUpdateError
from source_collector import collect_into
from source_inventory import SourceInventory
from source_store import atomic_bundle, digest, json_bytes, parse_time, read_json, utcnow
from maintenance_acceptance import acceptance_status
from maintenance_plan import collection_budget, explain, plan
from yckceo_harvester import YckceoHarvester
from shuyuans_harvester import ShuyuansHarvester
from submissions import collect_submissions


def keep_known_evidence(version, baseline):
    """A transport failure cannot un-read a source that already read.

    The evidence stays exactly as the last successful check left it; only the
    schedule moves, so the cool-down survives instead of being reset by every
    noisy run. The attempt itself is already recorded on the version.
    """
    health = version.setdefault('validation', {})
    resumed = deepcopy(baseline)
    resumed['consecutive_failures'] = int(baseline.get('consecutive_failures') or 0) + 1
    step = min(resumed['consecutive_failures'] - 1, len(COOL_DOWN_HOURS) - 1)
    stamp = (parse_time(health.get('last_check_at')) or parse_time(baseline.get('last_check_at'))
             or datetime.now(timezone.utc))
    resumed['last_check_at'] = stamp.isoformat()
    resumed['next_check_at'] = (stamp + timedelta(hours=COOL_DOWN_HOURS[step])).isoformat()
    resumed['next_deep_check_at'] = resumed['next_check_at']
    version['validation'] = resumed


class DailyMaintenance:
    def __init__(self, base_dir=None):
        self.inventory = SourceInventory(base_dir)
        self.base_dir = self.inventory.base_dir
        self.metadata_file = self.inventory.metadata_file

    def without_isolated_hosts(self, batch, report):
        """Skip a host that already refused this run instead of accusing it again."""
        isolated = self.inventory.isolated_hosts()
        if not isolated:
            return batch
        store = self.inventory.store
        kept = [ref for ref in batch
                if self.inventory.site(store.get(ref[0])['versions'][ref[1]]['source']) not in isolated]
        skipped = len(batch) - len(kept)
        if skipped:
            report['skipped_isolated'] = int(report.get('skipped_isolated') or 0) + skipped
        return kept

    def funnel(self, report, current):
        """Report where this run's probes went.

        A run that publishes nothing but re-reads what it already knows looks the
        same as a run that found nothing new. These counters separate the two.
        """
        prior = read_json(self.base_dir / 'main/publication.json', {})
        published = {item['source_id'] for item in prior.get('sources', [])}
        results = report.get('deep_results') or []
        first = sum(1 for item in results
                    if item.get('status') == 'valid' and item['source_id'] not in published)
        recheck = sum(1 for item in results
                      if item.get('status') == 'valid' and item['source_id'] in published)
        attempts = int(report.get('deep_checked') or 0)
        kinds = report.get('deep_kinds') or {}
        transport = sum(count for kind, count in kinds.items() if kind in TRANSPORT_FAILURES)
        engine = sum(count for kind, count in kinds.items() if kind in ENVIRONMENT_FAILURES)
        verified_before = int((prior.get('counts') or {}).get('verified', prior.get('count', 0)))
        return {
            'first_hit_valid': first,
            'recheck_valid': recheck,
            'transport_share': round(transport / attempts, 4) if attempts else 0.0,
            'engine_share': round(engine / attempts, 4) if attempts else 0.0,
            'verified_delta': int(current.get('verified_count', 0)) - verified_before,
            'terminal_parked': int((report.get('raw_progress') or {}).get('parked_revisions') or 0),
        }
    async def validate(self, validator, refs, *, mode, deadline, save, report):
        inventory, store = self.inventory, self.inventory.store
        baseline, completed = {}, []
        counts, kinds, examples = Counter(), Counter(), {}
        for offset in range(0, len(refs), 20):
            if report.get('engine_incident'):
                break
            if time.monotonic() >= deadline:
                report['budget_exhausted'] = True
                break
            batch = self.without_isolated_hosts(refs[offset:offset + 20], report)
            if not batch:
                continue
            for key, revision in batch:
                version = store.get(key)['versions'][revision]
                baseline[key, revision] = (deepcopy(version.get('validation', {})), deepcopy(version.get('audit', {})))
            tasks = [asyncio.create_task(validator.probe(inventory.audit_payload(store.get(key)['versions'][revision]), mode))
                     for key, revision in batch]
            try:
                _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - time.monotonic()))
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
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
            recent_sites = {inventory.site(store.get(ref[0])['versions'][ref[1]]['source']) for ref, _ in completed}
            network_failures = sum(result['kind'] in TRANSPORT_FAILURES for _, result in completed)
            # Fresh candidates carry no history, so the guard measures this run only.
            if len(completed) >= 20 and len(recent_sites) >= 5 and network_failures / len(completed) >= 0.6:
                report['network_incident'] = True
                # A shared transport failure is reported, not obeyed: the run keeps
                # working on the hosts that still answer, and the failing ones are
                # isolated instead of being re-probed by the next batch.
                for (key, revision), result in completed:
                    if result['kind'] not in TRANSPORT_FAILURES:
                        continue
                    if result.get('audit', {}).get('decision') in ('block', 'review'):
                        continue
                    prior = baseline[key, revision][0]
                    if prior.get('status') != 'valid':
                        # Nothing to protect: the failure is the newest evidence we have.
                        continue
                    keep_known_evidence(store.get(key)['versions'][revision], prior)
                    store.touch(key)
                failing = {inventory.site(store.get(ref[0])['versions'][ref[1]]['source'])
                           for ref, result in completed if result['kind'] in TRANSPORT_FAILURES}
                if failing:
                    report['hosts_isolated'] = inventory.isolate_hosts(failing, TRANSPORT_FAILURES)
            if save:
                store.save()
            print(f'{mode}: {len(completed)}/{len(refs)} {dict(counts)}', flush=True)
        report[mode] = dict(counts)
        report[mode + '_kinds'] = dict(kinds)
        report[mode + '_examples'] = examples
        report[mode + '_checked'] = len(completed)
        report[mode + '_results'] = [
            {'source_id': key, 'revision': revision,
             **{field: result[field] for field in ('status', 'kind', 'error', 'stages') if field in result}}
            for (key, revision), result in completed
        ]

    async def collect_sources(self, client, *, mode, deadline, save, fetch_cap, backfill_rounds, report):
        inventory, store = self.inventory, self.inventory.store

        async def submissions():
            return await collect_submissions(store, client, inventory.project_root, save=save, deadline=deadline)

        async def yckceo():
            cfg = inventory.config.get('yckceo', {})
            harvester = YckceoHarvester(inventory.policy, **cfg)
            provider_state = store.state.get('providers', {}).get('yckceo', {})
            last_scan = parse_time(provider_state.get('last_full_scan_at'))
            full_scan = mode == 'backfill' or not last_scan or datetime.now(timezone.utc) - last_scan > timedelta(days=7)
            batches = report['providers']['yckceo'] = []
            rounds = backfill_rounds if mode == 'backfill' else 1
            for round_index in range(rounds):
                result = await harvester.sync(
                    store, client, full_scan=full_scan, scan=round_index == 0,
                    daily_pages=int(cfg.get('daily_pages', 5)),
                    fetch_cap=fetch_cap if fetch_cap is not None else min(
                        int(cfg.get('emergency_fetch_cap' if mode in ('backfill', 'catchup') else 'daily_fetch_cap',
                                    600 if mode in ('backfill', 'catchup') else 300)),
                        # Each download costs about one request delay, so a cap that
                        # outlives the window only gets the provider cancelled with
                        # nothing reported. Half the window is the download share.
                        max(1, int(max(0.0, deadline - time.monotonic()) / 2))),
                    deadline=deadline, save=save,
                )
                batches.append(result)
                if time.monotonic() >= deadline or not result.get('fetched_ids') or not result.get('pending_count'):
                    break
            return batches

        async def shuyuans():
            """Harvest curated collections: the only intake that adds sources in bulk."""
            cfg = inventory.config.get('shuyuans', {})
            harvester = ShuyuansHarvester(inventory.policy, **cfg)
            return await harvester.sync(
                store, client, deadline=deadline, save=save,
                fetch_cap=fetch_cap if fetch_cap is not None else int(cfg.get('collections_per_run', 6)),
            )

        async def external():
            last = parse_time(store.state.get('last_external_collection_at'))
            if last and datetime.now(timezone.utc) - last <= timedelta(days=1):
                return {'skipped': 'Feeds were checked within the last day'}
            channels = read_json(inventory.project_root / 'config/source_channels.json', {})
            result = await collect_into(store, client, channels, deadline=deadline, save=save)
            if result['files'] and not result['errors']:
                store.state['last_external_collection_at'] = utcnow()
            return result

        async def run_provider(name, operation):
            try:
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    report['providers'][name] = await operation()
            except TimeoutError:
                report['collection_budget_exhausted'] = True
                report['providers'][name] = {'status': 'budget_exhausted',
                                             'completed': report['providers'].get(name)}
            except (OSError, ValueError, ProbeFailure) as exc:
                report['providers'][name] = {'error': str(exc)[:150]}

        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(run_provider('submissions', submissions))
                tasks.create_task(run_provider('yckceo', yckceo))
                tasks.create_task(run_provider('shuyuans', shuyuans))
                if mode != 'backfill':
                    tasks.create_task(run_provider('external', external))
        finally:
            if save:
                store.save()

    async def maintain(self, batch_size=None, concurrency=20, timeout=10, dry_run=False,
                       mode='daily', collect=True, publish=False, max_minutes=90, fetch_cap=None,
                       backfill_rounds=1, publish_extended=True):
        with writer_lock(self.base_dir):
            inventory = self.inventory
            inventory.store = __import__('source_store').SourceStore(inventory.store.root)
            store = inventory.store
            if not store.exists:
                if dry_run:
                    raise RuntimeError('Run source_inventory.py migrate before a dry run')
                inventory.ensure_migrated()
            report = {'schema_version': 4, 'started_at': utcnow(), 'mode': mode, 'dry_run': dry_run,
                      'published': False, 'providers': {}}
            deadline = time.monotonic() + max_minutes * 60
            # Reserve the harvesting window up front; validation will otherwise
            # consume the whole budget and collection never starts.
            collection_share = collection_budget(max_minutes, collect=collect)
            validation_deadline = deadline - collection_share
            report['static_audit'] = inventory.audit_all()
            if not dry_run:
                store.save()
            report['raw_progress_before'] = inventory.revalidation_progress()
            export, _, initial = inventory.select()
            decision = plan(verified_count=initial.get('verified_count', 0),
                            extended_count=initial.get('extended_count', 0),
                            reserve_count=initial.get('reserve_count', 0),
                            pending_revisions=report['raw_progress_before']['pending_revisions'],
                            config=inventory.config.get('inventory', {}), forced_mode=mode)
            report['plan'] = decision
            report['plan_summary'] = explain(decision, initial)
            if batch_size is None:
                batch_size = decision['batch_size']
            candidate_limit = batch_size if initial['needs_replenishment'] else 0
            refs = inventory.queue(limit=candidate_limit) if mode != 'backfill' else []
            scheduled = set(refs)
            light_refs = [(item['_source_id'], item['_revision']) for item in export
                          if (item['_source_id'], item['_revision']) not in scheduled]

            async def check_inventory():
                # Every probe starts its own JVM worker, so this number is also the
                # number of live engines: keep it under what the runner can hold
                # rather than matching the CLI default. A container capped at one
                # CPU cannot use more than the machine has, so the CPU count is the
                # ceiling no matter what the config asks for.
                requested = int(inventory.config.get('inventory', {}).get('validation_concurrency', 6))
                limit = max(2, min(requested, concurrency, os.cpu_count() or requested))
                request_timeout = float(inventory.config.get('validation', {}).get('engine_request_timeout', 30))
                validator = ReadingValidator(inventory.policy, concurrency=max(1, limit),
                                             request_timeout=request_timeout)
                async with asyncio.TaskGroup() as checks:
                    checks.create_task(self.validate(validator, refs, mode='deep', deadline=validation_deadline,
                                                     save=not dry_run, report=report))
                    if mode == 'daily':
                        checks.create_task(self.validate(validator, light_refs, mode='light', deadline=validation_deadline,
                                                         save=not dry_run, report=report))
                previous = read_json(inventory.base_dir / 'main/publication.json', {})
                if (mode == 'daily' and previous.get('schema_version') in (3, 4)
                        and not report.get('network_incident') and not report.get('engine_incident')):
                    day = datetime.now(timezone.utc).date().isoformat()
                    selected = sorted(previous.get('sources', []), key=lambda item: digest([day, item['source_id']]))[:50]
                    spot_refs = [(item['source_id'], item['revision']) for item in selected]
                    await self.validate(validator, spot_refs, mode='spot', deadline=validation_deadline,
                                        save=not dry_run, report=report)

            if mode != 'backfill':
                await check_inventory()
            progress = inventory.revalidation_progress()
            decision = inventory.collection_decision(progress, inventory.select()[2])
            if not collect:
                decision = {'allowed': False, 'reason': 'collection_disabled'}
            elif report.get('engine_incident'):
                decision = {'allowed': False, 'reason': 'validation_incident'}
            elif time.monotonic() >= deadline:
                decision = {'allowed': False, 'reason': 'run_budget_exhausted'}
            elif report.get('network_incident'):
                # Reading probes go to the source sites themselves. The feed
                # catalogues are different hosts, so a partial egress failure must
                # not stop harvesting; the collection budget already bounds it.
                decision = {**decision, 'warning': 'validation_network_incident'}
            report['collection_decision'] = decision
            if decision['allowed']:
                collection_deadline = (deadline if mode == 'backfill' else
                                       min(deadline, time.monotonic() + max(collection_share, 0.0)))
                # Feeds and collections are large JSON documents, so the client needs
                # the configured collection budget rather than the probe timeout.
                collection_timeout = float(inventory.config.get('collection', {}).get('timeout', 30))
                async with HTTPClient(concurrency, collection_timeout, max_bytes=32 * 1024 * 1024) as client:
                    await self.collect_sources(client, mode=mode, deadline=collection_deadline, save=not dry_run,
                                               fetch_cap=fetch_cap, backfill_rounds=backfill_rounds, report=report)
            # A newly collected exclusion must apply before selecting the release.
            # A newly collected revision must be audited; unchanged ones are cached.
            report['static_audit'] = inventory.audit_all(dirty_only=True)
            report['raw_progress'] = inventory.revalidation_progress()
            _, _, current = inventory.select()
            report['inventory'] = current
            report.update(self.funnel(report, current))
            report['publication_gate'] = {'verified': inventory.publication_gate(current),
                                          'extended': inventory.publication_gate(current, extended=True)}
            report['finished_at'] = utcnow()
            store.state.setdefault('runs', []).append({key: value for key, value in report.items()
                                                       if key != 'providers' and not key.endswith('_results')})
            store.state['runs'] = store.state['runs'][-30:]
            if not dry_run:
                store.save()
                inventory.write_shadow()
                if publish and mode != 'backfill':
                    # A transport incident is a property of the moment: the release
                    # follows the normal gates, and the shrink guard still owns the
                    # question of how small a snapshot may become.
                    has_prior = (inventory.base_dir / 'main/publication.json').exists()
                    verified_gate = report['publication_gate']['verified']
                    extended_gate = report['publication_gate']['extended']
                    if report.get('engine_incident'):
                        report['publication_skipped'] = 'Runtime incident; existing snapshot retained'
                    elif verified_gate['ready'] or extended_gate['ready'] or has_prior:
                        try:
                            inventory.publish(allow_empty=has_prior, publish_extended=publish_extended)
                            report['published'] = True
                            report['published_tiers'] = {
                                'verified': verified_gate['ready'],
                                'verified_skipped': None if verified_gate['ready'] else verified_gate['missing'],
                                'extended': extended_gate['ready'],
                            }
                        except SafeUpdateError as exc:
                            report['publication_skipped'] = str(exc)
                    else:
                        report['publication_skipped'] = ('Neither tier is publishable: '
                                                        + ', '.join(extended_gate['missing']))
                report['release_active'] = read_json(self.base_dir / 'main/publication.json', {}).get('schema_version') in (3, 4)
                store.state['runs'][-1] = {key: value for key, value in report.items()
                                         if key != 'providers' and not key.endswith('_results')}
                report['acceptance'] = acceptance_status(store.state['runs'])
                store.save()
                atomic_bundle({self.base_dir / 'main' / 'maintenance_report.json': json_bytes(report)})
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            return report


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path)
    parser.add_argument('--mode', choices=['daily', 'catchup', 'backfill'], default='daily')
    parser.add_argument('--batch-size', type=int)
    parser.add_argument('--concurrency', type=int, default=20)
    parser.add_argument('--timeout', type=int, default=10)
    parser.add_argument('--max-minutes', type=float, default=90)
    parser.add_argument('--fetch-cap', type=int)
    parser.add_argument('--backfill-rounds', type=int, default=1)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--no-collect', action='store_true')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--publish-extended', action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if ((args.batch_size is not None and args.batch_size < 0) or args.concurrency < 1
            or args.timeout <= 0 or args.max_minutes <= 0 or args.backfill_rounds < 1
            or (args.fetch_cap is not None and args.fetch_cap < 0)):
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
