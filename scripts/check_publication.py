"""Verify a release without contacting source websites."""
import argparse
import hashlib
import json
from pathlib import Path

from source_store import read_json

# A runner caps one step summary at 1 MiB; keep a wide margin and never grow with the data.
SUMMARY_LIMIT = 64 * 1024
SUMMARY_EXAMPLES = 3


def _counts(manifest):
    counts = manifest.get('counts') or {}
    return {
        'verified': int(counts.get('verified', manifest.get('count', 0))),
        'legacy': int(counts.get('legacy', 0)),
        'extended': int(counts.get('extended', manifest.get('count', 0))),
        'unverified': int(counts.get('unverified', 0)),
    }


def check(root, allow_bootstrap=False, current_health=False):
    base = root / 'sources/legado'
    manifest = read_json(base / 'main/publication.json')
    if not manifest:
        if allow_bootstrap:
            return {'status': 'bootstrap', 'count': len(read_json(base / 'full.json', [])), 'message': 'No verified release yet'}
        raise ValueError('Publication manifest is missing')
    public = (base / 'full.json').read_bytes()
    if public != (base / 'main/full.json').read_bytes():
        raise ValueError('Public source mirrors differ')
    if hashlib.sha256(public).hexdigest() != manifest['sha256']:
        raise ValueError('Publication checksum mismatch')
    sources = json.loads(public)
    if len(sources) != manifest['count'] or len({item['bookSourceUrl'] for item in sources}) != len(sources):
        raise ValueError('Publication count or identities are inconsistent')
    if read_json(root / 'docs/publication.json') != manifest:
        raise ValueError('Website publication manifest differs')
    counts = _counts(manifest)
    if manifest.get('schema_version') == 4:
        if counts['verified'] + counts['legacy'] != manifest['count']:
            raise ValueError('Verified and legacy counts differ from the published file')
        _check_extended(base, root, manifest, sources, counts, current_health)
    elif manifest.get('schema_version') == 3:
        if current_health:
            _require_health(root, manifest.get('sources', []))
        if manifest.get('healthy_count', 0) + manifest.get('grace_count', 0) != len(sources):
            raise ValueError('Publication health counts differ')
    readme = (root / 'README.md').read_text()
    index = (root / 'docs/index.html').read_text()
    if (f'严格验证书源 ({counts["verified"]} 个)' not in readme
            or f'{counts["extended"]} 个纯小说书源' not in index):
        raise ValueError('Visible publication counts differ')
    if manifest.get('schema_version') == 4 and f'扩展书源 ({counts["extended"]} 个' not in readme:
        raise ValueError('Visible extended counts differ')
    return manifest


def _require_health(root, refs):
    from source_inventory import SourceInventory
    from source_health import eligibility
    inventory = SourceInventory(root)
    for ref in refs:
        try:
            version = inventory.store.get(ref['source_id'])['versions'][ref['revision']]
        except KeyError as exc:
            raise ValueError('A published source is missing from the raw store') from exc
        if eligibility(version, inventory.policy.auditor.version) is None:
            raise ValueError('A published source lacks current reading or content evidence')


def _check_extended(base, root, manifest, sources, counts, current_health):
    extended_file = base / 'full-extended.json'
    if not extended_file.exists():
        raise ValueError('Extended package is missing')
    extended_bytes = extended_file.read_bytes()
    if extended_bytes != (base / 'main/full-extended.json').read_bytes():
        raise ValueError('Extended public mirrors differ')
    if hashlib.sha256(extended_bytes).hexdigest() != manifest.get('sha256_extended'):
        raise ValueError('Extended checksum mismatch')
    extended = json.loads(extended_bytes)
    if len(extended) != counts['extended'] or len(extended) < len(sources):
        raise ValueError('Extended count or prefix is inconsistent')
    if extended[:len(sources)] != sources:
        raise ValueError('Extended package does not start with the verified release')
    if len({item['bookSourceUrl'] for item in extended}) != len(extended):
        raise ValueError('Extended package repeats a source identity')
    tier_manifest = read_json(base / 'manifest.json', {})
    if tier_manifest.get('release_id') != manifest['release_id']:
        raise ValueError('Tier manifest belongs to another release')
    if len(tier_manifest.get('verified', [])) != counts['verified']:
        raise ValueError('Tier manifest verified count differs')
    if len(tier_manifest.get('extended_only', [])) != counts['extended'] - counts['verified']:
        raise ValueError('Tier manifest extended count differs')
    if current_health:
        _require_health(root, manifest.get('tiers', {}).get('verified', manifest.get('sources', [])))


def _targets(root):
    """Delivery targets live in config; fall back to the documented defaults."""
    inventory = read_json(root / 'config/supplement_config.json', {}).get('inventory', {})
    return int(inventory.get('verified_target', 950)), int(inventory.get('extended_target', 1000))


def _ranked(values, limit):
    return sorted(values.items(), key=lambda item: (-item[1], item[0]))[:limit]


def _top(values, count=5):
    return ', '.join(f'{name}={value}' for name, value in _ranked(values, count)) or 'none'


def summary_text(result, report=None, *, verified_target=950, extended_target=1000, limit=SUMMARY_LIMIT):
    """Render a step summary that stays well below the runner's 1 MiB cap."""
    report = report or {}
    counts = _counts(result)
    inventory = report.get('inventory') or {}
    progress = report.get('raw_progress') or {}
    deep = report.get('deep') or {}
    decision = report.get('collection_decision') or {}
    attempts = int(report.get('deep_checked') or 0)
    valid = int(deep.get('valid') or 0)
    share = counts['verified'] / counts['extended'] if counts['extended'] else 0.0

    lines = [
        '## Publication', '',
        f"- verified: {counts['verified']} / {verified_target}",
        f"- extended: {counts['extended']} / {extended_target}",
        f"- unverified: {counts['unverified']} ({share:.0%} verified)",
        f"- legacy: {counts['legacy']}",
        f"- status: `{result.get('status', 'unknown')}`, release `{result.get('release_id', 'unknown')}`",
        f"- published at: {result.get('published_at') or 'never'}",
        '', '## Maintenance run', '',
        f"- mode: `{report.get('mode', 'unknown')}`, finished: {report.get('finished_at') or 'unknown'}",
        f"- published: {bool(report.get('published'))}, dry run: {bool(report.get('dry_run'))}",
        f"- deep attempts: {attempts}, valid: {valid}"
        f" ({valid / attempts:.2%})" if attempts else '- deep attempts: 0, valid: 0',
        f"- outcomes: {_top(deep)}",
        f"- failure kinds: {_top(report.get('deep_kinds') or {})}",
        f"- collection: {'allowed' if decision.get('allowed') else 'blocked'}"
        f" ({decision.get('reason', 'unknown')})",
        f"- raw inventory: sources {progress.get('raw_sources', 'unknown')}"
        f", eligible {progress.get('eligible_revisions', 'unknown')}"
        f", attempted {progress.get('attempted_revisions', 'unknown')}"
        f", pending {progress.get('pending_revisions', 'unknown')}",
        f"- reserves: {inventory.get('reserve_count', 'unknown')}"
        f", healthy: {inventory.get('healthy_count', 'unknown')}",
    ]

    incidents = [name for name in ('engine_incident', 'network_incident') if report.get(name)]
    lines += ['', '## Incidents', '', f"- {', '.join(incidents) if incidents else 'none'}"]

    examples = report.get('deep_examples') or {}
    if examples:
        lines += ['', '## Examples', '']
        for kind in list(examples)[:SUMMARY_EXAMPLES]:
            item = examples[kind] or {}
            name = item.get('name', 'unknown')
            error = item.get('error', '')
            lines.append(f"- `{kind}`: {name}" + (f" ({error})" if error else ''))

    text = '\n'.join(lines) + '\n'
    encoded = text.encode()
    if len(encoded) <= limit:
        return text
    suffix = f"\n\n… truncated ({len(encoded)} bytes)"
    keep = max(0, limit - len(suffix.encode()))
    return encoded[:keep].decode('utf-8', 'ignore') + suffix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--allow-bootstrap', action='store_true')
    parser.add_argument('--summary', action='store_true',
                        help='Print a bounded Markdown summary for the run log')
    parser.add_argument('--full-report', action='store_true',
                        help='Print the whole maintenance report; local debugging only')
    parser.add_argument('--current-health', action='store_true')
    args = parser.parse_args()
    result = check(args.base_dir, args.allow_bootstrap, args.current_health)
    report = read_json(args.base_dir / 'sources/legado/main/maintenance_report.json', {})
    if args.summary:
        verified_target, extended_target = _targets(args.base_dir)
        print(summary_text(result, report, verified_target=verified_target,
                           extended_target=extended_target))
    elif args.full_report:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({key: value for key, value in result.items()
                          if key not in ('sources', 'tiers')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
