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
from source_health import (QUARANTINE_AFTER, VALIDATOR_VERSION, apply_result, eligibility,
                           structural_failures)
from source_policy import SourcePolicy, source_url_problem
from source_store import SourceStore, json_bytes, parse_time, read_json, rule_fingerprint, utcnow
from source_store import atomic_bundle, digest, source_id, source_payload
from publication_gate import publication_gate
from maintenance_lock import writer_lock
from runtime_config import runtime_fingerprint

SUFFIXES = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)

# The extended package keeps verified sources first and fills the rest with the
# best remaining guess. An untried source outranks one the engine already failed:
# the failed one carries evidence against it, the untried one does not.
TIERS = ('verified', 'grace', 'stale_verified', 'never_checked', 'unverified')
TIER_ORDER = {tier: index for index, tier in enumerate(TIERS)}
UNVERIFIED_KINDS = frozenset({
    'unsupported', 'rule_error', 'search_empty', 'toc_empty', 'book_empty', 'content_empty',
    'http_error', 'http_forbidden', 'auth_required', 'network', 'timeout', 'server_error',
    'rate_limit', 'tls_error', 'redirect_limit', 'challenge',
    'engine_timeout', 'engine_protocol', 'engine_crash', 'engine_unavailable',
    'no_search_rule',
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
        self.verified_share_target = float(cfg.get('verified_share_target', 0.3))
        # One truth: the absolute floor follows from the share and the package size.
        self.verified_target = round(self.extended_target * self.verified_share_target)
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
        """Cheap structural rejection, before any network work."""
        if source.get('enabled') is False:
            return 'disabled'
        if str(source.get('bookSourceType', 0)) != '0':
            return 'non_novel'
        return source_url_problem(source)

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
        share = len(export) / len(extended) if extended else 0.0
        # The searchable share is the real ceiling for verified delivery: an
        # explore-only source can be shipped but never verified.
        searchable = sum(1 for item in extended if str(item.get('searchUrl') or '').strip())
        report = {
            'timestamp': utcnow(), 'export_count': len(export), 'reserve_count': len(reserve),
            'grace_count': grace, 'ready_versions': len(ready), 'unique_sites': len(domains),
            'healthy_count': len(export) - grace,
            'verified_count': len(export), 'extended_count': len(extended),
            'extended_only_count': len(extended_only),
            'unverified_count': sum(item['_tier'] in ('unverified', 'never_checked') for item in extended_only),
            'verified_share': round(share, 4),
            'verified_share_target': self.verified_share_target,
            'searchable_count': searchable,
            'searchable_share': round(searchable / len(extended), 4) if extended else 0.0,
            'tiers': dict(Counter(item.get('_tier', 'verified') for item in extended)),
            'needs_replenishment': (len(extended) < self.extended_target
                                    or len(reserve) < self.reserve_target),
            'status': ('healthy' if share >= self.verified_share_target
                       and len(extended) >= self.extended_target else 'degraded'),
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
                # Only a search rule can ever produce reading evidence. Explore-only
                # sources stay deliverable, but they must not crowd out the ones that
                # can raise the verified share.
                '_searchable': bool(str(source.get('searchUrl') or '').strip()),
                '_failures': int(health.get('consecutive_failures') or 0),
                '_checked': health.get('last_check_at') or '',
                '_source': payload,
            })
        candidates.sort(key=lambda item: (TIER_ORDER[item['_tier']], not item['_searchable'],
                                          item['_failures'], -item['_rank'],
                                          item['_checked'], item['_source_id']), reverse=False)
        result = list(export)
        seen_urls = {item['bookSourceUrl'].split('#', 1)[0] for item in result}
        seen_rules = {rule_fingerprint(item) for item in result}
        domains = Counter(self.site(item) for item in result)
        for item in candidates:
            if len(result) >= min(self.extended_target, self.extended_max):
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
        """Harvesting is independent of the validation backlog.

        Waiting for the raw backlog to reach zero starved collection for weeks:
        every evidence reset restarted the backlog and shut the feeds down again.
        The run budget already caps collection at a share of the schedule, so the
        backlog only changes the reported reason.
        """
        if not inventory['needs_replenishment']:
            return {'allowed': False, 'reason': 'inventory_sufficient'}
        reason = 'raw_inventory_pending' if progress['pending_revisions'] else 'qualified_inventory_shortage'
        return {'allowed': True, 'reason': reason}

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
        # A third success on a site that already fills its quota cannot be
        # published, so those sources wait until nothing better is left. Sites
        # without evidence yet are the only place a new success adds a slot.
        covered = Counter(self.site(item) for item in export)
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
                if category == 2 and structural_failures(version) >= QUARANTINE_AFTER:
                    continue
                last_check = parse_time(health.get('last_check_at')) if category == 2 else last_deep
                priority = (2 if checked else 0 if last_deep else 1) if category == 2 else 0
                saturated = 1 if category == 2 and covered.get(
                    self.site(version['source']), 0) >= self.max_per_domain else 0
                pending.append((category, saturated, priority,
                                last_check or datetime.min.replace(tzinfo=timezone.utc),
                                -self.policy.enrich_source(version['source'])['selectionScore'], key, revision))
        pending.sort()
        cfg = self.config.get('inventory', {})
        quotas = {0: int(cfg.get('published_deep_batch', 250)), 1: int(cfg.get('reserve_deep_batch', 125)), 2: limit}
        selected = []
        for category, _, _, _, _, key, revision in pending:
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
        for record in self.store.records():
            latest = record['versions'][record['latest_revision']]
            audit = latest.get('audit', {})
            if (audit.get('decision') in ('block', 'review')
                    and audit.get('policy_version') == self.policy.auditor.version):
                blocked_urls.add(str(latest['source'].get('bookSourceUrl', '')).split('#', 1)[0])
        self.updater.safe_update(export, extended_sources=extended, report=report, extra_files=extras,
                                 allow_empty=allow_empty, publish_extended=publish_extended,
                                 blocked_urls=blocked_urls,
                                 rejected_urls=self.currently_refused(export),
                                 site_of=self.site)
        return report

    def currently_refused(self, export):
        """Published entries that a check under the current evidence identity rejects.

        An entry whose evidence merely predates the current identity is not refused:
        it stays published and is re-checked first, so an engine upgrade cannot empty
        the public list on its own.
        """
        export_urls = {str(item.get('bookSourceUrl', '')) for item in export}
        previous = read_json(self.base_dir / 'main' / 'publication.json', {})
        refs = (previous.get('tiers') or {}).get('verified') or previous.get('sources') or []
        refused = set()
        for ref in refs:
            key, revision = ref.get('source_id'), ref.get('revision')
            if not key or not revision:
                continue
            try:
                version = self.store.get(key)['versions'][revision]
            except (KeyError, TypeError):
                continue
            url = str(version['source'].get('bookSourceUrl', ''))
            if url in export_urls:
                continue
            health = version.get('validation', {})
            if (health.get('validator_version') == VALIDATOR_VERSION
                    and health.get('runtime_fingerprint') == runtime_fingerprint()
                    and health.get('last_check_at')
                    and health.get('status') != 'valid'):
                refused.add(url.split('#', 1)[0])
        return refused

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
