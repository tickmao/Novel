"""Keep one GitHub issue in sync with the automation's health."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from source_store import read_json

LABEL = 'maintenance-degraded'
TITLE = 'Maintenance degraded: delivery below target'


def summarize(root):
    manifest = read_json(root / 'sources/legado/main/publication.json', {})
    report = read_json(root / 'sources/legado/main/maintenance_report.json', {})
    counts = manifest.get('counts', {})
    verified = int(counts.get('verified', manifest.get('count', 0)))
    extended = int(counts.get('extended', verified))
    unverified = int(counts.get('unverified', 0))
    attempts = int(report.get('deep_checked', 0))
    valid = int((report.get('deep') or {}).get('valid', 0))
    kinds = report.get('deep_kinds') or {}
    top_kinds = sorted(kinds.items(), key=lambda item: item[1], reverse=True)[:5]
    incidents = [name for name in ('engine_incident', 'network_incident') if report.get(name)]
    return {
        'verified': verified, 'extended': extended, 'unverified': unverified,
        'verified_share': round(verified / extended, 4) if extended else 0.0,
        'attempts': attempts, 'valid': valid,
        'conversion': round(valid / attempts, 4) if attempts else 0.0,
        'top_kinds': top_kinds, 'incidents': incidents,
        'status': manifest.get('status', 'unknown'),
        'published_at': manifest.get('published_at'),
        'checked_at': report.get('finished_at'),
    }


def abnormal(summary, *, extended_target=1000, verified_share_target=0.3):
    """Judge delivery by the share of evidence-backed sources, not by a count.

    The absolute count was unreachable by design, so the alert was permanently on
    and stopped carrying information.
    """
    return (bool(summary['incidents']) or summary['extended'] < extended_target
            or summary['verified_share'] < verified_share_target)


def body_of(summary, run_url=None, extended_target=1000, verified_share_target=0.3):
    lines = [
        f"Delivery: verified **{summary['verified']}** ({summary['verified_share']:.0%}) / target"
        f" {verified_share_target:.0%}, extended **{summary['extended']}** / {extended_target}"
        f" (unverified {summary['unverified']}).",
        '',
        f"- verified share: {summary['verified_share']:.0%}",
        f"- last run conversion: {summary['valid']}/{summary['attempts']} = {summary['conversion']:.2%}",
        f"- top failure kinds: {', '.join(f'{name}={count}' for name, count in summary['top_kinds']) or 'none'}",
        f"- incidents: {', '.join(summary['incidents']) or 'none'}",
        f"- publication status: `{summary['status']}` ({summary['published_at'] or 'never'})",
    ]
    if run_url:
        lines += ['', f'Run: {run_url}']
    return '\n'.join(lines)


def gh(args, *, stdin=None, runner=None):
    runner = runner or subprocess.run
    return runner(['gh', *args], input=stdin, capture_output=True, text=True, check=False)


def sync_alert(summary, *, repo, run_url=None, extended_target=1000, verified_share_target=0.3, runner=None):
    """Create, update or close the single degraded-maintenance issue."""
    result = gh(['issue', 'list', '--repo', repo, '--label', LABEL, '--state', 'open', '--json', 'number'],
                runner=runner)
    try:
        open_issues = json.loads(result.stdout or '[]')
    except ValueError:
        open_issues = []
    number = open_issues[0]['number'] if open_issues else None
    if not abnormal(summary, extended_target=extended_target,
                    verified_share_target=verified_share_target):
        if number is None:
            return {'action': 'none', 'reason': 'healthy'}
        gh(['issue', 'close', str(number), '--repo', repo, '--comment', 'Delivery is back on target.'], runner=runner)
        return {'action': 'closed', 'number': number}
    body = body_of(summary, run_url, extended_target, verified_share_target)
    if number is not None:
        gh(['issue', 'comment', str(number), '--repo', repo, '--body-file', '-'], stdin=body, runner=runner)
        return {'action': 'commented', 'number': number}
    created = gh(['issue', 'create', '--repo', repo, '--title', TITLE, '--label', LABEL, '--body-file', '-'],
                 stdin=body, runner=runner)
    return {'action': 'created', 'stdout': (created.stdout or '').strip()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--repo', required=True)
    parser.add_argument('--run-url')
    parser.add_argument('--extended-target', type=int, default=1000)
    parser.add_argument('--verified-share-target', type=float, default=0.3)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    summary = summarize(args.base_dir)
    if args.dry_run:
        print(json.dumps({'summary': summary,
                          'abnormal': abnormal(summary, extended_target=args.extended_target,
                                               verified_share_target=args.verified_share_target)},
                         ensure_ascii=False, indent=2))
        return
    print(json.dumps(sync_alert(summary, repo=args.repo, run_url=args.run_url,
                                extended_target=args.extended_target,
                                verified_share_target=args.verified_share_target), ensure_ascii=False))


if __name__ == '__main__':
    main()
