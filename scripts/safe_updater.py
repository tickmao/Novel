"""The only writer for public Legado snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from source_health import eligibility
from source_policy import SourcePolicy
from source_store import atomic_bundle, digest, json_bytes, read_json, rule_fingerprint, source_payload, utcnow
from publication_gate import publication_gate
from runtime_config import VALIDATOR_VERSION, runtime_fingerprint
from maintenance_lock import writer_lock


class SafeUpdateError(RuntimeError):
    pass


def legado_dir(path=None):
    root = Path(path) if path else Path(__file__).resolve().parents[1]
    if root.name == 'legado' or (root / 'main').exists():
        return root
    return root / 'sources' / 'legado'


def stats_files(root: Path, count: int, manifest: dict) -> dict:
    files = {}
    readme = root / 'README.md'
    if readme.exists():
        value = readme.read_text()
        value = re.sub(r'(\| Android / iOS \| \[阅读 \(Legado\)\].*?\| )\d+( \|)', rf'\g<1>{count}\g<2>', value)
        value = re.sub(r'\*\*全量书源 \(\d+ 个\)\*\*', f'**全量书源 ({count} 个)**', value)
        note = f'<!-- publication-status -->\n当前发布：**{count}** 条；状态：`{manifest["status"]}`；更新时间：{manifest["published_at"]}。\n<!-- /publication-status -->'
        if '<!-- publication-status -->' in value:
            value = re.sub(r'<!-- publication-status -->.*?<!-- /publication-status -->', note, value, flags=re.S)
        else:
            value = value.replace('## 书源导入', note + '\n\n## 书源导入')
        files[readme] = value.encode()
    index = root / 'docs' / 'index.html'
    if index.exists():
        value = index.read_text()
        value = re.sub(r'<span class="stat-value">\d+ 个书源</span>', f'<span class="stat-value">{count} 个书源</span>', value)
        value = re.sub(r'<span class="source-count">\d+ 个纯小说书源</span>', f'<span class="source-count">{count} 个纯小说书源</span>', value)
        value = re.sub(r'聚合 \d+\+? 纯小说书源', f'聚合 {count} 纯小说书源', value)
        note = f'<p id="publication-status">当前发布 {count} 条 · {manifest["status"]} · {manifest["published_at"]}</p>'
        if 'id="publication-status"' in value:
            value = re.sub(r'<p id="publication-status">.*?</p>', note, value)
        else:
            value = value.replace('</main>', note + '\n</main>')
        files[index] = value.encode()
    files[root / 'STATS.md'] = (
        '# 书源统计报告\n\n'
        f'更新时间：{manifest["published_at"]}\n\n'
        f'- Legado 公开书源：{count}\n'
        f'- 合格备用书源：{manifest.get("reserve_count", 0)}\n'
        f'- 短期故障保留：{manifest.get("grace_count", 0)}\n'
        f'- 状态：`{manifest["status"]}`\n'
        f'- 版本：`{manifest["release_id"]}`\n\n'
        '数量来自同一份发布快照。原始库和待验证书源不计入交付数量。\n'
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
        self.compatibility_file = self.base_dir / 'full.json'
        self.backup_dir = self.main_dir / 'backups'
        self.policy = SourcePolicy(self.base_dir.parent.parent)
        self.config = read_json(self.base_dir.parent.parent / 'config/supplement_config.json', {}).get('inventory', {})
        self.REQUIRED_EXPORT_COUNT = int(self.config.get('export_target', 1000))

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

    def safe_update(self, new_sources, skip_validation=False, *, report=None, extra_files=None, allow_empty=False):
        with writer_lock(self.base_dir):
            return self._safe_update(new_sources, skip_validation, report=report,
                                     extra_files=extra_files, allow_empty=allow_empty)

    def _safe_update(self, new_sources, skip_validation=False, *, report=None, extra_files=None, allow_empty=False):
        if skip_validation:
            raise SafeUpdateError('Production validation cannot be bypassed')
        if not new_sources and not allow_empty:
            raise SafeUpdateError('An empty release needs a completed inventory decision')
        valid, reason = self.validate_sources(new_sources)
        if not valid:
            raise SafeUpdateError(reason)
        previous = read_json(self.main_dir / 'publication.json', {})
        report = dict(report or {})
        report['grace_count'] = sum(item.get('_eligibility') == 'grace' for item in new_sources)
        report['healthy_count'] = len(new_sources) - report['grace_count']
        gate = publication_gate(self.base_dir.parent.parent, report, previous)
        if not gate['ready']:
            raise SafeUpdateError('Publication gate: ' + ', '.join(gate['missing']))
        export = []
        for source in new_sources:
            item = source_payload(source)
            item['bookSourceName'] = self.policy.enrich_source(source)['bookSourceName']
            export.append(item)
        payload = (json.dumps(export, ensure_ascii=False, indent=2) + '\n').encode()
        content_hash = hashlib.sha256(payload).hexdigest()
        count = len(export)
        manifest = {
            'schema_version': 3, 'release_id': content_hash[:16],
            'published_at': utcnow(), 'count': count, 'sha256': content_hash,
            'status': 'healthy' if report['healthy_count'] >= 950 else 'degraded',
            'healthy_count': report['healthy_count'], 'validator_version': VALIDATOR_VERSION,
            'runtime_fingerprint': runtime_fingerprint(),
            'reserve_count': report.get('reserve_count', 0), 'grace_count': report.get('grace_count', 0),
            'policy_version': self.policy.auditor.version,
            'sources': [{'source_id': item.get('_source_id'), 'revision': item.get('_revision')} for item in new_sources],
        }
        protected = {self.main_file, self.compatibility_file, self.main_dir / 'publication.json'}
        if protected & set(extra_files or {}):
            raise SafeUpdateError('Extra files cannot replace the publication')
        files = {
            self.main_file: payload, self.compatibility_file: payload,
            self.main_dir / 'publication.json': json_bytes(manifest),
            **stats_files(self.base_dir.parent.parent, count, manifest),
            **(extra_files or {}),
        }
        if self.main_file.exists() and previous:
            backup_name = f'{previous["release_id"]}.json'
            files[self.backup_dir / backup_name] = json_bytes(previous)
        atomic_bundle(files)
        if self.main_file.read_bytes() != self.compatibility_file.read_bytes():
            raise SafeUpdateError('Public mirrors differ')
        backups = self.list_backups()
        for old in backups[self.MAX_BACKUPS:]:
            old.unlink()
        return True

    def rollback(self, backup_file=None, force=False):
        if force:
            raise SafeUpdateError('Rollback cannot bypass current safety checks')
        from source_inventory import SourceInventory

        backups = self.list_backups()
        path = Path(backup_file) if backup_file else (backups[0] if backups else None)
        if not path:
            return False
        snapshot = read_json(path)
        if not isinstance(snapshot, dict) or snapshot.get('schema_version') not in (2, 3):
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
