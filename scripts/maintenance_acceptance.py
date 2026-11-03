"""Measure seven completed daily runs; never infer acceptance from source counts alone."""

from datetime import datetime, timedelta, timezone
from source_store import parse_time


# A count of healthy sources was the acceptance bar while delivery was fixed at
# one thousand. Evidence-backed delivery moves with the pool, so the bar is now
# "the release is real, the spot check holds, and the run did enough work".
MIN_SPOT_CHECKS = 50
MIN_SPOT_VALID = 0.95
MIN_DEEP_ATTEMPTS = 150
MAX_EVIDENCE_AGE_DAYS = 7
SPOT_SKIPPED = frozenset({'Runtime incident; existing snapshot retained'})


def daily_outcome(run):
    """One day passes on release health plus a full-size spot check."""
    spot = run.get('spot') or {}
    checked = int(run.get('spot_checked') or 0)
    published = bool(run.get('published')) or run.get('publication_skipped') in SPOT_SKIPPED
    return {
        'release': run.get('schema_version') in (3, 4) and bool(run.get('release_active')),
        'published': published,
        'engine': not run.get('engine_incident'),
        'spot': checked >= MIN_SPOT_CHECKS and spot.get('valid', 0) / checked >= MIN_SPOT_VALID,
        'work': int(run.get('deep_checked') or 0) >= MIN_DEEP_ATTEMPTS,
        'fresh': evidence_is_current(run),
    }


def evidence_is_current(run, max_age_days=MAX_EVIDENCE_AGE_DAYS):
    """The oldest delivered check must still be inside the publication window.

    A run recorded before the freshness metric existed has nothing to answer with,
    so it is not failed for a number it could not have produced.
    """
    oldest = ((run.get('inventory') or {}).get('freshness') or {}).get('oldest_days')
    return oldest is None or oldest <= max_age_days


def acceptance_status(runs):
    days, progress = {}, {}
    for run in runs:
        stamp = parse_time(run.get('finished_at'))
        if run.get('mode') != 'daily' or not stamp:
            continue
        outcome = daily_outcome(run)
        days[stamp.date()] = all(outcome.values())
        progress[stamp.date()] = (int(run.get('raw_progress', {}).get('checked_revisions') or 0),
                                  int(run.get('inventory', {}).get('verified_count') or 0))
    ordered = sorted(days)[-7:]
    # A week of green checks that moved no evidence is not acceptance: the loop is
    # running, the delivery is not. Verified growth is reported, never required,
    # because the pool decides how much evidence exists to find.
    spun = len(ordered) >= 2 and progress[ordered[-1]] == progress[ordered[0]]
    complete = (len(ordered) == 7 and all(days[day] for day in ordered)
                and ordered[-1] - ordered[0] == timedelta(days=6)
                and not spun
                and datetime.now(timezone.utc).date() - ordered[-1] <= timedelta(days=1))
    return {'status': 'accepted' if complete else 'pending', 'observed_days': len(ordered),
            'days': [{'date': day.isoformat(), 'passed': days[day]} for day in ordered]}
