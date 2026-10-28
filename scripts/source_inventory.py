"""Select verified source versions and publish one consistent snapshot."""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import tldextract

from safe_updater import SafeUpdater, legado_dir
from source_health import apply_result, eligibility, VALIDATOR_VERSION
from source_policy import SourcePolicy
from source_store import SourceStore, json_bytes, parse_time, read_json, rule_fingerprint, utcnow
from source_store import atomic_bundle, digest, source_id, source_payload
from publication_gate import publication_gate
from maintenance_lock import writer_lock
from runtime_config import runtime_fingerprint

SUFFIXES = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)

# The extended package keeps verified sources first and fills the rest with
# sources that have never been shown to be dead. Nothing here proves readability.
TIERS = ('verified', 'grace', 'stale_verified', 'unverified', 'never_checked')
TIER_ORDER = {tier: index for index, tier in enumerate(TIERS)}
UNVERIFIED_KINDS = frozenset({
    'unsupported', 'rule_error', 'search_empty', 'toc_empty', 'book_empty', 'content_empty',
    'http_error', 'http_forbidden', 'auth_required', 'network', 'timeout', 'server_error',
    'rate_limit', 'tls_error', 'redirect_limit', 'challenge',
    'engine_timeout', 'engine_protocol', 'engine_crash', 'engine_unavailable',
})
EXCLUDED_KINDS = frozenset({'disabled', 'invalid_url', 'content_locked', 'content_audit'})


class SourceInventory:
    def __init__(self, base_dir=None):
        self.base_dir = legado_dir(base_dir)
        self.project_root = self.base_dir.parent.parent
        self.policy = SourcePolicy(self.project_root)
        self.updater = SafeUpdater(self.base_dir)
        self.store = SourceStore(self.base_dir / 'pool' / 'raw')
        self.config = read_json(self.project_root / 'config' / 'supplement_config.json', {})
        cfg = self.config.get('inventory', {})
        self.export_target = int(cfg.get('export_target', 1000))
        self.reserve_target = int(cfg.get('reserve_target', 500))
        self.reserve_min = int(cfg.get('reserve_min', 300))
        self.max_age_days = int(cfg.get('export_max_age_days', 7))
        self.grace_hours = int(cfg.get('grace_hours', 72))
        self.grace_limit = int(cfg.get('grace_limit', 50))
        self.max_per_domain = int(self.config.get('supplement', {}).get('max_per_domain', 2))
        self.extended_target = int(cfg.get('extended_target', self.export_target))
        self.extended_min = int(cfg.get('extended_min', 200))
        self.extended_max = int(cfg.get('extended_max', 1200))
        self.verified_target = int(cfg.get('verified_target', 950))
        self.max_per_domain_extended = int(cfg.get('max_per_domain_extended',
                                                   self.config.get('supplement', {}).get('max_per_domain_extended', 3)))
        self.stale_verified_days = int(cfg.get('stale_verified_days', 30))
        self.metadata_file = self.base_dir / 'main' / 'metadata.json'
        self.export_file = self.base_dir / 'main' / 'full.json'
        self.extended_file = self.base_dir / 'main' / 'full-extended.json'
        self.working_file = self.base_dir / 'main' / 'working.json'
        self.candidate_file = self.base_dir / 'pool' / 'candidates.json'

    def ensure_migrated(self):
        if not self.store.exists:
            return self.store.migrate([
                self.base_dir / 'pool' / 'raw.json', self.candidate_file,
                self.export_file, self.base_dir / 'full.json',
            ])
        return {'records': self.store.manifest['count']}

    def audit_payload(self, version):
        source = deepcopy(version['source'])
        source['_review_payload_hash'] = digest(source_payload(source))
        source['_health'] = deepcopy(version.get('validation', {}))
        titles = [item.get('title', '') for item in version.get('provenance', []) if item.get('title')]
        if titles:
            source['_yckceo_title'] = ' '.join(titles)
        return source

    @staticmethod
    def scope_reason(source):
        if source.get('enabled') is False:
            return 'disabled'
        if str(source.get('bookSourceType', 0)) != '0':
            return 'non_novel'
        return None

    def audit_all(self):
        from reading_validator import ReadingValidator
        from static_rules import UnsupportedRule
        preflight = ReadingValidator(None, self.policy)
        stats = Counter()
        for record in self.store.records():
            for version in record['versions'].values():
                payload = self.audit_payload(version)
                reason = self.scope_reason(version['source'])
                scope = {'eligible': reason is None, 'reason': reason}
                if version.get('scope') != scope:
                    version['scope'] = scope
                    self.store.touch(record['source_id'])
                if reason:
                    stats[reason] += 1
                result = self.policy.audit_source(payload)
                previous = version.get('audit', {})
                resolved = self.policy.auditor.resolve_review(previous, payload)
                if resolved != previous:
                    version['audit'] = resolved
                    version.setdefault('validation', {})['next_deep_check_at'] = utcnow()
                    self.store.touch(record['source_id'])
                if (result['decision'] != 'pass' or previous.get('policy_version') != result['policy_version']
                        or result.get('review') and previous.get('decision') == 'review'):
                    version['audit'] = result
                    self.store.touch(record['source_id'])
                stats[result['decision']] += 1
                if result['decision'] == 'pass' and not reason:
                    try:
                        preflight.supports(version['source'])
                    except (UnsupportedRule, TypeError, ValueError) as exc:
                        capabilities = {'static': False, 'reason': str(exc)}
                        if version.get('capabilities') != capabilities:
                            version['capabilities'] = capabilities
                            self.store.touch(record['source_id'])
                        stats['unsupported'] += 1
        return dict(stats)

    def site(self, source):
        host = (urlsplit(source.get('bookSourceUrl', '')).hostname or '').lower()
        aliases = self.config.get('domain_aliases', {})
        parsed = SUFFIXES(host)
        return aliases.get(host, parsed.top_domain_under_public_suffix or host or source_id(source))

    def ready_versions(self, now=None, allowed=None):
        now = now or datetime.now(timezone.utc)
        result = []
        previous = read_json(self.base_dir / 'main' / 'publication.json', {})
        published = {item['source_id'] for item in previous.get('sources', [])}
        excluded = set()
        for record in self.store.records():
            latest = record['versions'][record['latest_revision']]
            audit = latest.get('audit', {})
            if audit.get('decision') == 'block' and audit.get('policy_version') == self.policy.auditor.version:
                excluded.add(self.site(latest['source']))
        for record in self.store.records():
            latest = record['versions'][record['latest_revision']]
            latest_audit = latest.get('audit', {})
            # A revised adult source must not fall back to an older approved version.
            if latest_audit.get('decision') in ('block', 'review') or self.site(latest['source']) in excluded:
                continue
            candidates = []
            for revision, version in record['versions'].items():
                if allowed is not None and (record['source_id'], revision) not in allowed:
                    continue
                state = eligibility(version, self.policy.auditor.version, now, self.max_age_days, self.grace_hours)
                if not state:
                    continue
                source = self.audit_payload(version)
                if self.policy.audit_source(source)['decision'] != 'pass':
                    continue
                if source.get('enabled') is False or str(source.get('bookSourceType', 0)) != '0':
                    continue
                enriched = self.policy.enrich_source(source)
                enriched.update({
                    '_source_id': record['source_id'], '_revision': revision,
                    '_audit': deepcopy(version['audit']), '_health': deepcopy(version['validation']),
                    '_eligibility': state,
                })
                successes = sum(item.get('status') == 'valid' and item.get('mode') == 'deep'
                                for item in version.get('attempts', []))
                latency = min(float(version.get('validation', {}).get('response_ms') or 0), 20000)
                enriched['_rank'] = (enriched['selectionScore'] + min(successes, 10) * 2
                                     + (5 if record['source_id'] in published else 0) - latency / 5000)
                candidates.append((state == 'grace', revision != record['latest_revision'], enriched))
            if candidates:
                candidates.sort(key=lambda value: value[:2])
                result.append(candidates[0][2])
        return sorted(result, key=lambda item: (item['_eligibility'] == 'grace', -item['_rank'], item['_source_id']))

    def select(self, now=None, allowed=None):
        ready = self.ready_versions(now, allowed)
        export, reserve, seen_urls, seen_rules = [], [], set(), set()
        domains = Counter()
        reserve_domains = Counter()
        grace = 0
        for source in ready:
            url, fingerprint = source['bookSourceUrl'], rule_fingerprint(source)
            if url in seen_urls or fingerprint in seen_rules:
                continue
            state, domain = source['_eligibility'], self.site(source)
            grace_allowed = grace < self.grace_limit and (grace + 1) * 20 <= len(export) + 1
            if len(export) < self.export_target and domains[domain] < self.max_per_domain and (state != 'grace' or grace_allowed):
                export.append(source)
                domains[domain] += 1
                grace += state == 'grace'
                seen_urls.add(url)
                seen_rules.add(fingerprint)
            elif state == 'ready' and len(reserve) < self.reserve_target and reserve_domains[domain] < self.max_per_domain:
                reserve_domains[domain] += 1
                reserve.append(source)
                seen_urls.add(url)
                seen_rules.add(fingerprint)
        extended = self.extended_versions(export, now)
        extended_only = extended[len(export):]
        report = {
            'timestamp': utcnow(), 'export_count': len(export), 'reserve_count': len(reserve),
            'grace_count': grace, 'ready_versions': len(ready), 'unique_sites': len(domains),
            'healthy_count': len(export) - grace,
            'verified_count': len(export), 'extended_count': len(extended),
            'extended_only_count': len(extended_only),
            'unverified_count': sum(item['_tier'] in ('unverified', 'never_checked') for item in extended_only),
            'verified_share': round(len(export) / len(extended), 4) if extended else 0.0,
            'tiers': dict(Counter(item.get('_tier', 'verified') for item in extended)),
            'needs_replenishment': (len(extended) < self.extended_target
                                    or len(reserve) < self.reserve_target),
            'status': 'healthy' if len(export) - grace >= self.verified_target else 'degraded',
        }
        return export, reserve, report

    def extended_versions(self, export, now=None):
        """Verified sources first, then the best available unverified candidates."""
        now = now or datetime.now(timezone.utc)
        verified = {(item['_source_id'], item['_revision']) for item in export}
        verified_ids = {item['_source_id'] for item in export}
        stale_after = now - timedelta(days=self.stale_verified_days)
        candidates = []
        for record in self.store.records():
            latest = record['versions'][record['latest_revision']]
            source = latest['source']
            if self.scope_reason(source):
                continue
            if source.get('webView') or 'startBrowser' in json.dumps(source):
                continue
            latest_audit = latest.get('audit', {})
            if (latest_audit.get('decision') in ('block', 'review')
                    and latest_audit.get('policy_version') == self.policy.auditor.version):
                continue
            health = latest.get('validation', {})
            if health.get('kind') in EXCLUDED_KINDS:
                continue
            state = eligibility(latest, self.policy.auditor.version, now, self.max_age_days, self.grace_hours)
            if (record['source_id'], record['latest_revision']) in verified:
                tier = 'verified'
            elif record['source_id'] in verified_ids:
                tier = 'verified'
            elif state == 'grace':
                tier = 'grace'
            elif parse_time(health.get('last_deep_success_at')) and parse_time(
                    health.get('last_deep_success_at')) >= stale_after:
                tier = 'stale_verified'
            elif health.get('kind') in UNVERIFIED_KINDS or health.get('status') in (
                    'transient', 'unverified', 'unsupported', 'invalid'):
                tier = 'unverified'
            else:
                tier = 'never_checked'
            payload = self.audit_payload(latest)
            if self.policy.audit_source(payload)['decision'] != 'pass':
                continue
            enriched = self.policy.enrich_source(payload)
            candidates.append({
                '_source_id': record['source_id'], '_revision': record['latest_revision'],
                '_tier': tier, '_rank': enriched['selectionScore'],
                '_failures': int(health.get('consecutive_failures') or 0),
                '_checked': health.get('last_check_at') or '',
                '_source': payload,
            })
        candidates.sort(key=lambda item: (TIER_ORDER[item['_tier']], item['_failures'], -item['_rank'],
                                          item['_checked'], item['_source_id']), reverse=False)
        result = list(export)
        seen_urls = {item['bookSourceUrl'].split('#', 1)[0] for item in result}
        seen_rules = {rule_fingerprint(item) for item in result}
        domains = Counter(self.site(item) for item in result)
        for item in candidates:
            if len(result) >= self.extended_max:
                break
            payload = item['_source']
            url, fingerprint = payload['bookSourceUrl'], rule_fingerprint(payload)
            domain = self.site(payload)
            limit = self.max_per_domain_extended if item['_tier'] != 'verified' else self.max_per_domain
            key = url.split('#', 1)[0]
            if key in seen_urls or fingerprint in seen_rules or domains[domain] >= limit:
                continue
            payload['_tier'] = item['_tier']
            payload['_source_id'] = item['_source_id']
            payload['_revision'] = item['_revision']
            result.append(payload)
            seen_urls.add(key)
            seen_rules.add(fingerprint)
            domains[domain] += 1
        return result
    def candidate_revisions(self, record):
        latest = record['latest_revision']
        revisions = {latest}
        if not self.scope_reason(record['versions'][latest]['source']):
            revisions.update(revision for revision, version in record['versions'].items()
                             if version.get('validation', {}).get('last_deep_success_at'))
        return revisions

    @staticmethod
    def deep_checked(version):
        check = version.get('validation', {}).get('deep_check', {})
        return (check.get('validator_version') == VALIDATOR_VERSION
                and check.get('runtime_fingerprint') == runtime_fingerprint()
                and bool(check.get('checked_at')))

    def revalidation_progress(self):
        sources = eligible_sources = eligible = checked = attempted = 0
        outcomes = Counter()
        for record in self.store.records():
            sources += 1
            attempted += sum(self.deep_checked(version) for version in record['versions'].values())
            latest = record['versions'][record['latest_revision']]
            audit = latest.get('audit', {})
            if audit.get('decision') in ('block', 'review') and audit.get('policy_version') == self.policy.auditor.version:
                continue
            count = 0
            for revision in self.candidate_revisions(record):
                version = record['versions'][revision]
                audit = version.get('audit', {})
                if self.scope_reason(version['source']) or (
                        audit.get('decision') in ('block', 'review')
                        and audit.get('policy_version') == self.policy.auditor.version):
                    continue
                count += 1
                if self.deep_checked(version):
                    checked += 1
                    outcomes[version['validation']['deep_check']['status']] += 1
            eligible += count
            eligible_sources += bool(count)
        return {'raw_sources': sources, 'eligible_sources': eligible_sources, 'attempted_revisions': attempted,
                'eligible_revisions': eligible, 'checked_revisions': checked,
                'pending_revisions': eligible - checked, 'outcomes': dict(outcomes)}

    def collection_decision(self, progress, inventory):
        if not inventory['needs_replenishment']:
            reason = 'inventory_sufficient'
        elif progress['pending_revisions']:
            reason = 'raw_inventory_pending'
        else:
            reason = 'qualified_inventory_shortage'
        return {'allowed': reason == 'qualified_inventory_shortage', 'reason': reason}

    def queue(self, limit=300, now=None):
        now = now or datetime.now(timezone.utc)
        export, reserve, _ = self.select(now)
        publication = read_json(self.base_dir / 'main/publication.json', {})
        shadow = read_json(self.base_dir / 'main/shadow.json', {})
        published = {item['source_id']: item['revision'] for item in shadow.get('sources', [])}
        published.update({item['source_id']: item['revision'] for item in publication.get('sources', [])})
        published.update({item['_source_id']: item['_revision'] for item in export})
        standby = {item['source_id']: item['revision'] for item in shadow.get('reserve', [])}
        standby.update({item['_source_id']: item['_revision'] for item in reserve})
        pending = []
        for record in self.store.records():
            key = record['source_id']
            revisions = self.candidate_revisions(record)
            for mapping in (published, standby):
                if key in mapping and mapping[key] in record['versions']:
                    revisions.add(mapping[key])
            latest_audit = record['versions'][record['latest_revision']].get('audit', {})
            if latest_audit.get('decision') in ('block', 'review') and latest_audit.get('policy_version') == self.policy.auditor.version:
                continue
            for revision in revisions:
                version = record['versions'][revision]
                if self.scope_reason(version['source']):
                    continue
                audit, health = version.get('audit', {}), version.get('validation', {})
                next_attempt = parse_time(health.get('next_attempt_at'))
                if next_attempt and next_attempt > now:
                    continue
                if audit.get('decision') in ('block', 'review') and audit.get('policy_version') == self.policy.auditor.version:
                    continue
                same_runtime = (health.get('validator_version') == VALIDATOR_VERSION
                                and health.get('runtime_fingerprint') == runtime_fingerprint())
                checked = self.deep_checked(version)
                if health.get('status') == 'unsupported' and same_runtime and checked:
                    continue
                next_check = parse_time(health.get('next_deep_check_at', health.get('next_check_at')))
                if next_check and next_check > now and same_runtime and checked:
                    continue
                last_deep = parse_time(health.get('last_deep_success_at'))
                category = 0 if published.get(key) == revision else 1 if standby.get(key) == revision else 2
                last_check = parse_time(health.get('last_check_at')) if category == 2 else last_deep
                priority = (2 if checked else 0 if last_deep else 1) if category == 2 else 0
                pending.append((category, priority, last_check or datetime.min.replace(tzinfo=timezone.utc),
                                -self.policy.enrich_source(version['source'])['selectionScore'], key, revision))
        pending.sort()
        cfg = self.config.get('inventory', {})
        quotas = {0: int(cfg.get('published_deep_batch', 250)), 1: int(cfg.get('reserve_deep_batch', 125)), 2: limit}
        selected = []
        for category, _, _, _, key, revision in pending:
            if quotas[category] > 0:
                selected.append((key, revision))
                quotas[category] -= 1
        return selected

    def record_result(self, key, revision, result):
        record = self.store.get(key)
        result = {**result, 'source_id': key, 'revision': revision}
        apply_result(record['versions'][revision], result)
        self.store.touch(key)

    def publication_gate(self, report=None, *, extended=False):
        previous = read_json(self.base_dir / 'main/publication.json', {})
        return publication_gate(self.project_root, report or self.select()[2], previous, extended=extended)

    def write_shadow(self):
        export, reserve, report = self.select()
        extended = self.extended_versions(export)
        summary = {
            'schema_version': 4, 'generated_at': utcnow(), 'inventory': report,
            'gate': self.publication_gate(report),
            'extended_gate': self.publication_gate(report, extended=True),
            'sources': [{'source_id': item['_source_id'], 'revision': item['_revision']}
                        for item in export],
            'extended_only': [{'source_id': item['_source_id'], 'revision': item['_revision'],
                               'tier': item.get('_tier', 'verified')}
                              for item in extended[len(export):]],
            'reserve': [{'source_id': item['_source_id'], 'revision': item['_revision']}
                        for item in reserve],
        }
        atomic_bundle({self.base_dir / 'main/shadow.json': json_bytes(summary)})
        return summary

    def publish(self, *, allowed=None, allow_empty=False, publish_extended=True):
        with writer_lock(self.base_dir):
            return self._publish(allowed=allowed, allow_empty=allow_empty,
                                 publish_extended=publish_extended)

    def _publish(self, *, allowed=None, allow_empty=False, publish_extended=True):
        export, reserve, report = self.select(allowed=allowed)
        extended = self.extended_versions(export)
        report['extended_count'] = len(extended)
        report['extended_only_count'] = len(extended) - len(export)
        metadata = read_json(self.metadata_file, {})
        metadata.update(last_maintenance=utcnow(), inventory=report, schema_version=4)
        working = export + reserve[:self.reserve_target]
        extras = {
            self.working_file: json_bytes(working), self.candidate_file: json_bytes(reserve),
            self.metadata_file: json_bytes(metadata),
        }
        blocked_urls = set()
        if not export:
            for record in self.store.records():
                latest = record['versions'][record['latest_revision']]
                audit = latest.get('audit', {})
                if (audit.get('decision') in ('block', 'review')
                        and audit.get('policy_version') == self.policy.auditor.version):
                    blocked_urls.add(str(latest['source'].get('bookSourceUrl', '')).split('#', 1)[0])
        self.updater.safe_update(export, extended_sources=extended, report=report, extra_files=extras,
                                 allow_empty=allow_empty, publish_extended=publish_extended,
                                 blocked_urls=blocked_urls)
        return report

    def inventory_status(self):
        export, reserve, report = self.select()
        report['raw_count'] = self.store.manifest['count']
        report['raw_progress'] = self.revalidation_progress()
        report['validation_statuses'] = dict(Counter(
            (record['versions'][record['latest_revision']].get('validation', {}).get('status', 'pending')
             if record['versions'][record['latest_revision']].get('validation', {}).get('validator_version') == VALIDATOR_VERSION
             else 'unverified')
            for record in self.store.records()))
        report['content_statuses'] = dict(Counter(
            record['versions'][record['latest_revision']].get('audit', {}).get('decision', 'pending')
            for record in self.store.records()))
        report['gate'] = {'verified': self.publication_gate(report),
                          'extended': self.publication_gate(report, extended=True)}
        channels = Counter()
        for item in export + reserve:
            version = self.store.get(item['_source_id'])['versions'][item['_revision']]
            for channel in {p.get('channel', p.get('provider', 'unknown')) for p in version.get('provenance', [])}:
                channels[channel] += 1
        report['qualified_by_channel'] = dict(channels)
        return report

    def load_raw_sources(self):
        if self.store.exists:
            return [record['versions'][record['latest_revision']]['source'] for record in self.store.records()]
        return read_json(self.base_dir / 'pool' / 'raw.json', [])

    def load_working_sources(self):
        return read_json(self.working_file, read_json(self.export_file, []))

    def load_candidate_sources(self):
        return read_json(self.candidate_file, [])


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'migrate', 'audit', 'rebuild', 'extended-status'])
    parser.add_argument('--base-dir', type=Path)
    args = parser.parse_args()
    inventory = SourceInventory(args.base_dir)
    if args.action == 'status':
        result = inventory.inventory_status()
    elif args.action == 'extended-status':
        export, reserve, report = inventory.select()
        extended = inventory.extended_versions(export)
        result = {'inventory': report,
                  'verified': [item['_source_id'] for item in export],
                  'extended_only': [{'source_id': item['_source_id'], 'tier': item.get('_tier', 'verified')}
                                    for item in extended[len(export):]]}
    else:
        with writer_lock(inventory.base_dir):
            if args.action == 'migrate':
                result = inventory.ensure_migrated()
            elif args.action == 'audit':
                result = inventory.audit_all()
                inventory.store.save()
            else:
                result = inventory.publish()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
