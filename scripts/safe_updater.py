"""The only writer for public Legado snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from source_health import eligibility
from source_policy import SourcePolicy, source_url_problem
from source_store import atomic_bundle, json_bytes, read_json, rule_fingerprint, source_payload, utcnow
from publication_gate import compatibility_evidence
from runtime_config import VALIDATOR_VERSION, runtime_fingerprint
from maintenance_lock import writer_lock


class SafeUpdateError(RuntimeError):
    pass


def within_site_limit(prefix, tail, site_of, limit):
    """Keep the appended tail inside the per-site budget of the whole package.

    The tail is selected before the carried entries join the prefix, so without
    this the merged package can hold one extra source per site.
    """
    if site_of is None or limit <= 0:
        return tail
    counts = Counter(site_of(item) for item in prefix)
    kept = []
    for item in tail:
        site = site_of(item)
        if counts[site] >= limit:
            continue
        counts[site] += 1
        kept.append(item)
    return kept


def legado_dir(path=None):
    root = Path(path) if path else Path(__file__).resolve().parents[1]
    if root.name == 'legado' or (root / 'main').exists():
        return root
    return root / 'sources' / 'legado'



def stats_files(root: Path, manifest: dict) -> dict:
    count = manifest['count']
    counts = manifest.get('counts', {})
    extended = counts.get('extended', count)
    verified = counts.get('verified', count)
    legacy = counts.get('legacy', 0)
    unverified = counts.get('unverified', 0)
    share = counts.get('verified_share', 1.0)
    legacy_note = f'（另含 {legacy} 条旧版本兼容保留）' if legacy else ''
    files = {}
    readme = root / 'README.md'
    if readme.exists():
        value = readme.read_text()
        value = re.sub(r'(\| Android / iOS \| \[阅读 \(Legado\)\].*?\| )\d+( \|)', rf'\g<1>{extended}\g<2>', value)
        value = re.sub(r'\*\*严格验证书源 \(\d+ 个\)(?:（另含 \d+ 条旧版本兼容保留）)?\*\*',
                       f'**严格验证书源 ({verified} 个){legacy_note}**', value)
        value = re.sub(r'\*\*扩展书源 \(\d+ 个，其中未验证 \d+ 个\)\*\*',
                       f'**扩展书源 ({extended} 个，其中未验证 {unverified} 个)**', value)
        note = (f'<!-- publication-status -->\n当前发布：严格验证 **{verified}** 条 / 扩展 **{extended}** 条'
                f'（未验证 {unverified} 条，验证占比 {share:.0%}）；状态：`{manifest["status"]}`；'
                f'更新时间：{manifest["published_at"]}。\n<!-- /publication-status -->')
        if '<!-- publication-status -->' in value:
            value = re.sub(r'<!-- publication-status -->.*?<!-- /publication-status -->', note, value, flags=re.S)
        else:
            value = value.replace('## 书源导入', note + '\n\n## 书源导入')
        files[readme] = value.encode()
    index = root / 'docs' / 'index.html'
    if index.exists():
        value = index.read_text()
        value = re.sub(r'<span class="stat-value">\d+ 个书源</span>', f'<span class="stat-value">{extended} 个书源</span>', value)
        value = re.sub(r'<span class="source-count">\d+ 个纯小说书源</span>', f'<span class="source-count">{extended} 个纯小说书源</span>', value)
        value = re.sub(r'聚合 \d+\+? 纯小说书源', f'聚合 {extended} 纯小说书源', value)
        # The page shows the delivered count only. Release state and the evidence
        # timestamp belong to README.md and STATS.md; a status line on the landing
        # page competed with the card layout and told a reader nothing.
        files[index] = value.encode()
    files[root / 'STATS.md'] = (
        '# 书源统计报告\n\n'
        f'更新时间：{manifest["published_at"]}\n\n'
        f'- 严格验证书源：{verified}\n'
        f'- 旧版本兼容保留：{legacy}\n'
        f'- 扩展交付书源：{extended}\n'
        f'- 扩展中未验证：{unverified}（验证占比 {share:.0%}）\n'
        f'- 合格备用书源：{manifest.get("reserve_count", 0)}\n'
        f'- 短期故障保留：{manifest.get("grace_count", 0)}\n'
        f'- 状态：`{manifest["status"]}`\n'
        f'- 版本：`{manifest["release_id"]}`\n\n'
        '数量来自同一份发布快照。原始库和待验证书源不计入交付数量；扩展包内未验证源不构成可用性证明。\n'
    ).encode()
    files[root / 'docs' / 'publication.json'] = json_bytes(manifest)
    return files


class SafeUpdater:
    MAX_BACKUPS = 10
    REQUIRED_EXPORT_COUNT = 1000
    MIN_SOURCES = 0
    MAX_SOURCES = 1000

    def __init__(self, base_dir=None):
        self.base_dir = legado_dir(base_dir)
        self.main_dir = self.base_dir / 'main'
        self.main_file = self.main_dir / 'full.json'
        self.extended_file = self.main_dir / 'full-extended.json'
        self.compatibility_file = self.base_dir / 'full.json'
        self.extended_compatibility_file = self.base_dir / 'full-extended.json'
        self.manifest_file = self.base_dir / 'manifest.json'
        self.backup_dir = self.main_dir / 'backups'
        self.policy = SourcePolicy(self.base_dir.parent.parent)
        settings = read_json(self.base_dir.parent.parent / 'config/supplement_config.json', {})
        self.config = settings.get('inventory', {})
        self.REQUIRED_EXPORT_COUNT = int(self.config.get('export_target', 1000))
        self.EXTENDED_MIN = int(settings.get('publication', {}).get('extended_min',
                                                                   self.config.get('extended_min', 200)))
        self.EXTENDED_MAX = int(self.config.get('extended_max', 1200))
        self.EXTENDED_PER_SITE = int(self.config.get('max_per_domain_extended', 3))

    def validate_sources(self, sources):
        if len(sources) > self.REQUIRED_EXPORT_COUNT:
            return False, 'Export exceeds its target'
        urls, fingerprints = set(), set()
        for item in sources:
            if not isinstance(item, dict):
                return False, 'Source must be an object'
            if not eligibility({'audit': item.get('_audit', {}), 'validation': item.get('_health', {})}, self.policy.auditor.version, max_age_days=int(self.config.get('export_max_age_days', 7)), grace_hours=int(self.config.get('grace_hours', 72))):
                return False, 'A source lacks current reading and content checks'
            if self.policy.audit_source(item)['decision'] != 'pass':
                return False, 'A source violates the current content policy'
            if item.get('enabled') is False or str(item.get('bookSourceType', 0)) != '0':
                return False, 'Only enabled novel sources can be exported'
            url = item.get('bookSourceUrl')
            fingerprint = rule_fingerprint(item)
            if not url or url in urls or fingerprint in fingerprints:
                return False, 'Duplicate or empty source identity'
            urls.add(url)
            fingerprints.add(fingerprint)
        grace_limit = min(int(self.config.get('grace_limit', 50)), len(sources) // 20)
        if sum(item.get('_eligibility') == 'grace' for item in sources) > grace_limit:
            return False, 'Grace limit exceeded'
        return True, 'OK'

    def validate_extended(self, tail, prefix):
        """Only the appended tail needs checking: the prefix is the verified release."""
        if not tail:
            return True, 'OK'
        urls = {str(item.get('bookSourceUrl', '')).split('#', 1)[0] for item in prefix}
        fingerprints = {rule_fingerprint(item) for item in prefix}
        for item in tail:
            if not isinstance(item, dict):
                return False, 'Extended source must be an object'
            if item.get('enabled') is False or str(item.get('bookSourceType', 0)) != '0':
                return False, 'Only enabled novel sources can be exported'
            if self.policy.audit_source(item)['decision'] != 'pass':
                return False, 'An extended source violates the current content policy'
            url = str(item.get('bookSourceUrl', '')).split('#', 1)[0]
            fingerprint = rule_fingerprint(item)
            if not url or url in urls or fingerprint in fingerprints:
                return False, 'Duplicate or empty source identity'
            urls.add(url)
            fingerprints.add(fingerprint)
        return True, 'OK'

    def safe_update(self, new_sources, skip_validation=False, *, extended_sources=None, report=None,
                    extra_files=None, allow_empty=False, publish_extended=True, blocked_urls=frozenset(),
                    rejected_urls=frozenset(), site_of=None):
        with writer_lock(self.base_dir):
            return self._safe_update(new_sources, skip_validation, extended_sources=extended_sources,
                                     report=report, extra_files=extra_files, allow_empty=allow_empty,
                                     publish_extended=publish_extended, blocked_urls=blocked_urls,
                                     rejected_urls=rejected_urls, site_of=site_of)

    def _safe_update(self, new_sources, skip_validation=False, *, extended_sources=None, report=None,
                     extra_files=None, allow_empty=False, publish_extended=True, blocked_urls=frozenset(),
                     rejected_urls=frozenset(), site_of=None):
        if skip_validation:
            raise SafeUpdateError('Production validation cannot be bypassed')
        if not new_sources and not allow_empty and not extended_sources:
            raise SafeUpdateError('An empty release needs a completed inventory decision')
        previous = read_json(self.main_dir / 'publication.json', {})
        report = dict(report or {})
        counts = {
            'healthy_count': int(report.get('healthy_count', len(new_sources))),
            'reserve_count': int(report.get('reserve_count', 0)),
            'extended_count': int(report.get('extended_count', len(extended_sources or new_sources))),
        }
        evidence = compatibility_evidence(self.base_dir.parent.parent)
        if not evidence['fixtures']:
            raise SafeUpdateError('Publication gate: engine_compatibility')
        valid, reason = self.validate_sources(new_sources)
        if not valid:
            raise SafeUpdateError(reason)
        # The strict file only holds evidence-backed sources. A previous entry stays
        # unless the current content policy blocks it or a check under the current
        # evidence identity already rejected it. An older identity is not a rejection:
        # an engine upgrade on its own must never shrink the release.
        entries = json.loads(self.main_file.read_bytes()) if self.main_file.exists() else []
        kept = [item for item in entries
                if self.policy.audit_source(item)['decision'] == 'pass'
                and source_url_problem(item) is None
                and str(item.get('bookSourceUrl', '')).split('#', 1)[0] not in blocked_urls
                and str(item.get('bookSourceUrl', '')).split('#', 1)[0] not in rejected_urls]
        legacy_tier = []
        if new_sources:
            new_payload, _, _ = self._payload(new_sources)
            fresh_urls = {str(item.get('bookSourceUrl', '')) for item in new_payload}
            fresh_rules = {rule_fingerprint(item) for item in new_payload}
            room = max(0, self.REQUIRED_EXPORT_COUNT - len(new_payload))
            carried = [item for item in kept
                       if str(item.get('bookSourceUrl', '')) not in fresh_urls
                       and rule_fingerprint(item) not in fresh_rules][:room]
            known_urls = fresh_urls | {str(item.get('bookSourceUrl', '')) for item in carried}
            known_rules = fresh_rules | {rule_fingerprint(item) for item in carried}
            # A full release drops the stale remainder by the export target, which is a
            # reason of its own; anything else must name a current rejection.
            capped = len(new_payload) + len(carried) >= self.REQUIRED_EXPORT_COUNT
            unjustified = [item for item in entries
                           if not capped
                           and str(item.get('bookSourceUrl', '')) not in known_urls
                           and rule_fingerprint(item) not in known_rules
                           and self.policy.audit_source(item)['decision'] == 'pass'
                           and source_url_problem(item) is None
                           and str(item.get('bookSourceUrl', '')).split('#', 1)[0] not in rejected_urls
                           and str(item.get('bookSourceUrl', '')).split('#', 1)[0] not in blocked_urls]
            if unjustified:
                raise SafeUpdateError('Release would shrink without a current rejection')
            prefix = new_payload + carried
            prefix_bytes = (json.dumps(prefix, ensure_ascii=False, indent=2) + '\n').encode()
            prefix_hash = hashlib.sha256(prefix_bytes).hexdigest()
            verified_tier = [{'source_id': item.get('_source_id'), 'revision': item.get('_revision')}
                             for item in new_sources]
            legacy_tier = [{'url': item['bookSourceUrl'], 'tier': 'legacy'} for item in carried]
            evidence_count = len(new_sources)
        else:
            if not kept and not allow_empty:
                raise SafeUpdateError('Publication gate: no verified sources')
            prefix = kept
            prefix_bytes = (json.dumps(kept, ensure_ascii=False, indent=2) + '\n').encode()
            prefix_hash = hashlib.sha256(prefix_bytes).hexdigest()
            verified_tier = []
            legacy_tier = [{'url': item['bookSourceUrl'], 'tier': 'legacy'} for item in kept]
            evidence_count = 0
        extended_payload, extended_only, extended_bytes = None, [], None
        tail_tiers = []
        if publish_extended:
            # A carried entry already sits in the prefix, so the tail is whatever the
            # prefix does not cover; taking a positional slice would repeat it.
            prefix_urls = {str(item.get('bookSourceUrl', '')).split('#', 1)[0] for item in prefix}
            prefix_rules = {rule_fingerprint(item) for item in prefix}
            tail = [item for item in (extended_sources or [])
                    if str(item.get('bookSourceUrl', '')).split('#', 1)[0] not in prefix_urls
                    and rule_fingerprint(item) not in prefix_rules]
            room = max(0, self.EXTENDED_MAX - len(prefix))
            tail = within_site_limit(prefix, tail, site_of, self.EXTENDED_PER_SITE)
            tail = tail[:room]
            valid, reason = self.validate_extended(tail, prefix)
            if not valid:
                raise SafeUpdateError(reason)
            entries = prefix + [source_payload(item) for item in tail]
            # The floor only matters for a first package; refreshing an existing one
            # keeps the strict and extended files consistent after any evidence change.
            minimum = 0 if self.extended_file.exists() else self.EXTENDED_MIN
            if len(entries) < minimum:
                report['extended_skipped'] = (f'below bootstrap floor '
                                              f'({len(entries)} < {self.EXTENDED_MIN})')
            else:
                extended_payload = (json.dumps(entries, ensure_ascii=False, indent=2) + '\n').encode()
                extended_bytes = hashlib.sha256(extended_payload).hexdigest()
                tail_tiers = [{'source_id': item.get('_source_id'), 'revision': item.get('_revision'),
                               'tier': item.get('_tier', 'unverified')} for item in tail]

        previous_counts = previous.get('counts', {})
        previous_tiers = previous.get('tiers', {})
        delivered = len(prefix)
        if extended_payload is not None:
            # `delivered` already counts the carried entries, so only the appended tail
            # adds to the package size; counting the legacy tier again would inflate it.
            extended_count = delivered + len(tail_tiers)
            unverified = sum(item['tier'] in ('unverified', 'never_checked') for item in tail_tiers)
            extended_only = legacy_tier + tail_tiers
        else:
            # A skipped refresh keeps the extended package that is already published.
            extended_count = int(previous_counts.get('extended', delivered))
            unverified = int(previous_counts.get('unverified', 0))
            extended_only = previous_tiers.get('extended_only', [])
        manifest = {
            'schema_version': 4, 'release_id': prefix_hash[:16],
            'published_at': utcnow(), 'count': delivered, 'sha256': prefix_hash,
            'status': report.get('status', 'degraded'),
            'healthy_count': counts['healthy_count'], 'validator_version': VALIDATOR_VERSION,
            'runtime_fingerprint': runtime_fingerprint(),
            'reserve_count': counts['reserve_count'], 'grace_count': int(report.get('grace_count', 0)),
            'policy_version': self.policy.auditor.version,
            'counts': {'verified': evidence_count, 'legacy': len(legacy_tier),
                       'extended': extended_count, 'extended_only': len(extended_only),
                       'unverified': unverified,
                       'verified_share': round(evidence_count / extended_count, 4) if extended_count else 0.0},
            'tiers': {'verified': verified_tier, 'extended_only': extended_only},
            'sha256_full': prefix_hash,
            'sha256_extended': extended_bytes or previous.get('sha256_extended'),
            'sources': verified_tier,
        }
        protected = {self.main_file, self.extended_file, self.compatibility_file,
                     self.extended_compatibility_file, self.manifest_file,
                     self.main_dir / 'publication.json'}
        if protected & set(extra_files or {}):
            raise SafeUpdateError('Extra files cannot replace the publication')
        files = {
            self.main_dir / 'publication.json': json_bytes(manifest),
            **stats_files(self.base_dir.parent.parent, manifest),
            **(extra_files or {}),
        }
        files.update({self.main_file: prefix_bytes, self.compatibility_file: prefix_bytes})
        if extended_payload is not None:
            files.update({self.extended_file: extended_payload,
                          self.extended_compatibility_file: extended_payload,
                          self.manifest_file: json_bytes(self._tier_manifest(manifest, extended_only))})
        if previous:
            files[self.backup_dir / f'{previous["release_id"]}.json'] = json_bytes(previous)
        atomic_bundle(files)
        if self.main_file.read_bytes() != self.compatibility_file.read_bytes():
            raise SafeUpdateError('Public mirrors differ')
        if extended_payload is not None and self.extended_file.read_bytes() != self.extended_compatibility_file.read_bytes():
            raise SafeUpdateError('Extended mirrors differ')
        backups = self.list_backups()
        for old in backups[self.MAX_BACKUPS:]:
            old.unlink()
        return True

    def _payload(self, sources):
        export = []
        for source in sources:
            item = source_payload(source)
            item['bookSourceName'] = self.policy.enrich_source(source)['bookSourceName']
            export.append(item)
        payload = (json.dumps(export, ensure_ascii=False, indent=2) + '\n').encode()
        return export, payload, hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _tier_manifest(manifest, extended_only):
        """Public lookup of verification state per source URL."""
        return {
            'schema_version': 1, 'published_at': manifest['published_at'],
            'release_id': manifest['release_id'], 'counts': manifest['counts'],
            'verified': [item['source_id'] for item in manifest['tiers']['verified']],
            'extended_only': extended_only,
        }

    def rollback(self, backup_file=None, force=False):
        if force:
            raise SafeUpdateError('Rollback cannot bypass current safety checks')
        from source_inventory import SourceInventory

        backups = self.list_backups()
        path = Path(backup_file) if backup_file else (backups[0] if backups else None)
        if not path:
            return False
        snapshot = read_json(path)
        if not isinstance(snapshot, dict) or snapshot.get('schema_version') not in (2, 3, 4):
            raise SafeUpdateError('Rollback requires a versioned publication manifest')
        allowed = {(item['source_id'], item['revision']) for item in snapshot.get('sources', [])}
        inventory = SourceInventory(self.base_dir)
        inventory.publish(allowed=allowed)
        return True

    def get_current_sources(self):
        return read_json(self.main_file, [])

    def list_backups(self):
        return sorted((path for path in self.backup_dir.glob('*.json')
                       if re.fullmatch(r'[0-9a-f]{16}\.json', path.name)),
                      key=lambda path: path.stat().st_mtime, reverse=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path)
    parser.add_argument('--list-backups', action='store_true')
    parser.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    updater = SafeUpdater(args.base_dir)
    if args.list_backups:
        for path in updater.list_backups():
            print(path)
    elif args.rollback:
        updater.rollback(args.rollback)


if __name__ == '__main__':
    main()
