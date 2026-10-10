"""Measure seven completed daily runs; never infer acceptance from source counts alone."""

from datetime import datetime, timedelta, timezone
from source_store import parse_time


def acceptance_status(runs):
    days = {}
    for run in runs:
        stamp = parse_time(run.get('finished_at'))
        if run.get('mode') != 'daily' or not stamp:
            continue
        spot = run.get('spot', {})
        checked = run.get('spot_checked', 0)
        passed = (run.get('schema_version') == 3 and run.get('release_active', False)
                  and run.get('inventory', {}).get('healthy_count', 0) >= 950
                  and checked >= 50 and spot.get('valid', 0) / checked >= 0.95
                  and not run.get('engine_incident') and not run.get('network_incident'))
        days[stamp.date()] = passed
    ordered = sorted(days)[-7:]
    complete = (len(ordered) == 7 and all(days[day] for day in ordered)
                and ordered[-1] - ordered[0] == timedelta(days=6)
                and datetime.now(timezone.utc).date() - ordered[-1] <= timedelta(days=1))
    return {'status': 'accepted' if complete else 'pending', 'observed_days': len(ordered),
            'days': [{'date': day.isoformat(), 'passed': days[day]} for day in ordered]}
