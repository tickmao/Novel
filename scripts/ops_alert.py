"""Keep one GitHub issue in sync with the automation's health."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from source_store import parse_time, read_json

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
    inventory = report.get('inventory') or {}
    progress = recent_progress(read_json(root / 'sources/legado/pool/raw/state.json', {}))
    return {
        'verified': verified, 'extended': extended, 'unverified': unverified,
        'verified_share': round(verified / extended, 4) if extended else 0.0,
        'attempts': attempts, 'valid': valid,
        'conversion': round(valid / attempts, 4) if attempts else 0.0,
        'top_kinds': top_kinds, 'incidents': incidents,
        'status': manifest.get('status', 'unknown'),
        'published_at': manifest.get('published_at'),
        'checked_at': report.get('finished_at'),
        'verified_delta': int(report.get('verified_delta') or 0),
        'transport_share': float(report.get('transport_share') or 0.0),
        'usable_target': int(inventory.get('usable_target') or USABLE_TARGET),
        'freshness': inventory.get('freshness') or {},
        'spinning': is_spinning(progress),
        'week': progress,
    }


def recent_progress(state, days=7):
    """Probe and delivery totals for the runs of the last week."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    for run in state.get('runs', []):
        stamp = parse_time(run.get('started_at')) or parse_time(run.get('finished_at'))
        if not stamp or stamp < cutoff:
            continue
        rows.append({'checked': int((run.get('raw_progress') or {}).get('checked_revisions') or 0),
                     'verified': int((run.get('inventory') or {}).get('verified_count') or 0)})
    return rows


def is_spinning(rows, minimum_runs=4):
    """Probes moved but delivery did not: the loop is running, the result is not."""
    if len(rows) < minimum_runs:
        return False
    first, last = rows[0], rows[-1]
    return last['checked'] > first['checked'] and last['verified'] <= first['verified']


USABLE_TARGET = 500
STALE_PUBLICATION_DAYS = 3
# Evidence older than the publication window is not evidence: the source could have
# changed the day after the check.
MAX_EVIDENCE_AGE_DAYS = 7


def spinning(summary):
    """A week of probes that changed no evidence is the failure worth reporting."""
    return bool(summary.get('spinning'))


def abnormal(summary, *, extended_target=1000, verified_target=USABLE_TARGET,
             stale_days=STALE_PUBLICATION_DAYS):
    """Delivery is behind when it has too few usable sources, or stale ones.

    The target is absolute because "usable" is the product: 500 sources that read.
    Everything else here is a condition a person can act on.
    """
    return (bool(summary['incidents'])
            or int(summary.get('verified') or 0) < int(summary.get('usable_target') or verified_target)
            or summary['extended'] < extended_target
            or spinning(summary) or stale(summary, stale_days)
            or stale_evidence(summary))


def stale_evidence(summary, max_age_days=MAX_EVIDENCE_AGE_DAYS):
    """The oldest delivered check is older than the publication window."""
    oldest = (summary.get('freshness') or {}).get('oldest_days')
    return oldest is not None and oldest > max_age_days


def stale(summary, days=STALE_PUBLICATION_DAYS):
    published = parse_time(summary.get('published_at'))
    if not published:
        return True
    return datetime.now(timezone.utc) - published > timedelta(days=days)


def body_of(summary, run_url=None, extended_target=1000, verified_target=None):
    week = summary.get('week') or []
    delta = (week[-1]['verified'] - week[0]['verified']) if len(week) >= 2 else 0
    target = int(verified_target or summary.get('usable_target') or USABLE_TARGET)
    fresh = summary.get('freshness') or {}
    lines = [
        f"Delivery: usable **{summary['verified']}** / {target}"
        f" ({summary['verified_share']:.0%} of the {summary['extended']}-entry package).",
        '',
        f"- verified share: {summary['verified_share']:.0%}",
        f"- evidence age: oldest {fresh.get('oldest_days')}d, median {fresh.get('median_days')}d",
        f"- verified change this week: {delta:+d}",
        f"- last run conversion: {summary['valid']}/{summary['attempts']} = {summary['conversion']:.2%}",
        f"- transport share of failures: {summary.get('transport_share', 0.0):.0%}",
        f"- top failure kinds: {', '.join(f'{name}={count}' for name, count in summary['top_kinds']) or 'none'}",
        f"- spinning: {'yes (probes moved, delivery did not)' if spinning(summary) else 'no'}",
        f"- incidents: {', '.join(summary['incidents']) or 'none'}",
        f"- publication status: `{summary['status']}` ({summary['published_at'] or 'never'})",
    ]
    if run_url:
        lines += ['', f'Run: {run_url}']
    return '\n'.join(lines)


def gh(args, *, stdin=None, runner=None):
    runner = runner or subprocess.run
    return runner(['gh', *args], input=stdin, capture_output=True, text=True, check=False)


def sync_alert(summary, *, repo, run_url=None, extended_target=1000,
               verified_target=None, runner=None):
    """Create, update or close the single degraded-maintenance issue."""
    result = gh(['issue', 'list', '--repo', repo, '--label', LABEL, '--state', 'open', '--json', 'number'],
                runner=runner)
    try:
        open_issues = json.loads(result.stdout or '[]')
    except ValueError:
        open_issues = []
    number = open_issues[0]['number'] if open_issues else None
    if not abnormal(summary, extended_target=extended_target,
                    verified_target=int(verified_target or summary.get('usable_target') or USABLE_TARGET)):
        if number is None:
            return {'action': 'none', 'reason': 'healthy'}
        gh(['issue', 'close', str(number), '--repo', repo, '--comment', 'Delivery is back on target.'], runner=runner)
        return {'action': 'closed', 'number': number}
    body = body_of(summary, run_url, extended_target, verified_target)
    if number is not None:
        gh(['issue', 'comment', str(number), '--repo', repo, '--body-file', '-'], stdin=body, runner=runner)
        return {'action': 'commented', 'number': number}
    # A missing label makes `gh issue create` fail, and the failure is invisible
    # because the step tolerates errors. Ensure the label first; --force is idempotent.
    gh(['label', 'create', LABEL, '--repo', repo, '--color', 'ededed', '--force',
        '--description', 'Automated maintenance delivery is below target'], runner=runner)
    created = gh(['issue', 'create', '--repo', repo, '--title', TITLE, '--label', LABEL, '--body-file', '-'],
                 stdin=body, runner=runner)
    if created.returncode:
        raise SystemExit(f'gh issue create failed: {(created.stderr or "").strip()}')
    return {'action': 'created', 'stdout': (created.stdout or '').strip()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--repo', required=True)
    parser.add_argument('--run-url')
    parser.add_argument('--extended-target', type=int, default=1000)
    parser.add_argument('--verified-target', type=int, default=USABLE_TARGET,
                        help='Usable sources the delivery must reach')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    summary = summarize(args.base_dir)
    if args.dry_run:
        print(json.dumps({'summary': summary,
                          'abnormal': abnormal(summary, extended_target=args.extended_target,
                                               verified_target=args.verified_target)},
                         ensure_ascii=False, indent=2))
        return
    print(json.dumps(sync_alert(summary, repo=args.repo, run_url=args.run_url,
                                extended_target=args.extended_target,
                                verified_target=args.verified_target), ensure_ascii=False))


if __name__ == '__main__':
    main()
