"""Compare committed legacy snapshots with archived evidence without publishing."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from source_inventory import SourceInventory
from source_store import digest, rule_fingerprint, source_id, utcnow

ROOT = Path(__file__).resolve().parents[1]


def git_bytes(revision, path):
    return subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=ROOT)


def classify(retained, audits, health):
    if retained:
        return 'retained'
    if any(audit.get('decision') in ('block', 'review') for audit in audits):
        return 'content_flag_requires_review'
    status = health.get('status', 'pending')
    if status == 'unsupported':
        return 'validator_unsupported'
    if status == 'transient':
        return 'temporary_failure'
    if status == 'invalid':
        return 'probe_failure_unconfirmed'
    if status == 'valid':
        return 'valid_status_not_retained'
    return 'not_verified'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', default='dfb374935e299b8c04476a91e3d978acf424b1e0')
    args = parser.parse_args()
    revision = subprocess.check_output(['git', 'rev-parse', args.revision], cwd=ROOT, text=True).strip()
    output = ROOT / 'reports/legacy-audit'
    inventory = SourceInventory(ROOT)
    records = {record['source_id']: record for record in inventory.store.records()}
    public_path = ROOT / 'sources/legado/full.json'
    current_bytes = public_path.read_bytes()
    current = json.loads(current_bytes)
    current_by_url = {item['bookSourceUrl']: item for item in current}
    rows, baselines, snapshots = [], {}, {}
    legacy_urls, legacy_rules = set(), set()
    for label, path in [('public', 'sources/legado/full.json'), ('internal', 'sources/legado/main/full.json')]:
        raw = git_bytes(revision, path)
        sources = json.loads(raw)
        snapshots[f'baseline-{label}.json'] = raw
        before = len(rows)
        for index, source in enumerate(sources):
            url = source['bookSourceUrl']
            key, exact = source_id(source), digest(source)
            fingerprint = rule_fingerprint(source)
            legacy_urls.add(url)
            legacy_rules.add(fingerprint)
            record = records.get(key, {})
            versions = record.get('versions', {})
            version = versions.get(exact, {})
            health = version.get('validation', {})
            static = inventory.policy.audit_source(source)
            stored = version.get('audit', {})
            latest = versions.get(record.get('latest_revision'), {})
            retained = url in current_by_url
            row = {
                'baseline': label, 'index': index, 'name': source.get('bookSourceName'), 'url': url,
                'source_id': key, 'baseline_revision': exact, 'exact_payload_archived': bool(version),
                'retained_url': retained,
                'retained_rules': retained and rule_fingerprint(current_by_url[url]) == fingerprint,
                'category': classify(retained, [static, stored, latest.get('audit', {})], health),
                'baseline_validation': health, 'baseline_static_audit': static,
                'baseline_stored_audit': stored, 'latest_revision': record.get('latest_revision'),
                'latest_audit': latest.get('audit', {}),
                'same_rule_versions': [
                    {'revision': rev, 'validation': value.get('validation', {}),
                     'audit': value.get('audit', {}), 'provenance': value.get('provenance', [])}
                    for rev, value in versions.items() if rule_fingerprint(value['source']) == fingerprint
                ],
            }
            rows.append(row)
        subset = rows[before:]
        baselines[label] = {
            'path': path, 'count': len(sources), 'sha256': hashlib.sha256(raw).hexdigest(),
            'exact_payloads_archived': sum(row['exact_payload_archived'] for row in subset),
            'retained_urls': sum(row['retained_url'] for row in subset),
            'retained_rules': sum(row['retained_rules'] for row in subset),
            'categories': dict(Counter(row['category'] for row in subset)),
            'validation_statuses': dict(Counter(row['baseline_validation'].get('status', 'pending') for row in subset)),
            'failure_kinds': dict(Counter(row['baseline_validation'].get('kind', 'none') for row in subset)),
        }
    current_rows = []
    for item in current:
        record = records.get(source_id(item), {})
        provenance = [p for version in record.get('versions', {}).values() for p in version.get('provenance', [])
                      if rule_fingerprint(version['source']) == rule_fingerprint(item)]
        current_rows.append({
            'url': item['bookSourceUrl'], 'name': item.get('bookSourceName'),
            'url_in_legacy': item['bookSourceUrl'] in legacy_urls,
            'rules_in_legacy': rule_fingerprint(item) in legacy_rules,
            'providers': sorted({p.get('provider', '') for p in provenance}),
        })
    report = {
        'generated_at': utcnow(), 'baseline_commit': revision,
        'method': 'Exact payloads retain their own validation evidence; other revisions are reported separately. '
                  'Categories are review queues, not proof of site failure or adult content. No network checks were run.',
        'baselines': baselines, 'legacy_unique_urls': len(legacy_urls),
        'current': {'count': len(current), 'sha256': hashlib.sha256(current_bytes).hexdigest(),
                    'urls_in_legacy': sum(row['url_in_legacy'] for row in current_rows),
                    'rules_in_legacy': sum(row['rules_in_legacy'] for row in current_rows)},
        'current_sources': current_rows, 'rows': rows,
    }
    assert len(rows) == sum(value['count'] for value in baselines.values())
    for baseline in baselines.values():
        assert sum(baseline['categories'].values()) == baseline['count']
    assert public_path.read_bytes() == current_bytes
    output.mkdir(parents=True, exist_ok=True)
    for name, raw in snapshots.items():
        (output / name).write_bytes(raw)
    (output / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key not in ('rows', 'current_sources')}, indent=2))


if __name__ == '__main__':
    main()
