"""Validation transitions and publication eligibility."""

from datetime import datetime, timedelta, timezone
from copy import deepcopy

from source_store import parse_time
from runtime_config import ENVIRONMENT_FAILURES, VALIDATOR_VERSION, runtime_fingerprint

# A structural failure says the rule does not fit the site: retrying it weekly
# spends the run budget re-reading a corpse. Transport and access failures are a
# property of the moment, so those keep their retries.
STRUCTURAL_FAILURES = frozenset({
    'rule_error', 'search_empty', 'toc_empty', 'book_empty', 'content_empty',
    'unsupported', 'no_search_rule',
})

# Transport failures are still a property of the moment, so they never become
# terminal on their own. They do get a growing cool-down: a host that keeps
# refusing us must not be re-probed on every run.
TRANSPORT_FAILURES = frozenset({
    'network', 'timeout', 'server_error', 'tls_error', 'rate_limit',
})
ACCESS_FAILURES = frozenset({'http_forbidden', 'auth_required'})
# Cooling steps by consecutive failure count; the last value repeats.
COOL_DOWN_HOURS = (1, 6, 24, 72, 168)
QUARANTINE_AFTER = 3
# Two certificate failures are enough: a broken chain does not repair itself today.
TLS_TERMINAL_AFTER = 2
# An access gate can be momentary, so it takes one more attempt than TLS.
ACCESS_TERMINAL_AFTER = 3
REPEATED_TERMINAL_AFTER = 6
DEFAULT_TERMINAL_RECHECK_DAYS = 30


def attempt_kinds(version, since=None):
    """Failure kinds recorded for this revision, optionally only after a moment.

    A parked revision that comes back must be judged on what happened since it was
    parked, otherwise the same attempt history parks it again immediately.
    """
    kinds = []
    for attempt in version.get('attempts') or []:
        if since:
            stamp = parse_time(attempt.get('checked_at'))
            if not stamp or stamp <= since:
                continue
        kinds.append(attempt.get('kind'))
    return kinds


def terminal(version, now=None):
    """Return the stored parking record while it is still in force, else ``None``."""
    now = now or datetime.now(timezone.utc)
    parked = version.get('validation', {}).get('terminal') or {}
    if not parked.get('kind'):
        return None
    until = parse_time(parked.get('recheck_after'))
    return parked if until and until > now else None


def park(version, now=None, recheck_days=DEFAULT_TERMINAL_RECHECK_DAYS):
    """Store a parking record once the attempts prove this revision cannot pass.

    Parking is a decision with a date, not a deletion: ``recheck_after`` brings the
    revision back without any code change, and the attempt history stays readable.
    """
    now = now or datetime.now(timezone.utc)
    health = version.setdefault('validation', {})
    if terminal(version, now):
        return health['terminal']
    since = parse_time((health.get('terminal') or {}).get('parked_at'))
    kinds = attempt_kinds(version, since)
    failures = int(health.get('consecutive_failures') or 0) if not since else len(kinds)
    reason = None
    if sum(kind in STRUCTURAL_FAILURES for kind in kinds) >= QUARANTINE_AFTER:
        reason = 'structural'
    elif kinds.count('tls_error') >= TLS_TERMINAL_AFTER:
        reason = 'tls'
    elif sum(kind in ACCESS_FAILURES for kind in kinds) >= ACCESS_TERMINAL_AFTER:
        reason = 'access'
    elif failures >= REPEATED_TERMINAL_AFTER:
        reason = 'repeated'
    if not reason:
        return None
    health['terminal'] = {'kind': reason, 'parked_at': now.isoformat(),
                          'recheck_after': (now + timedelta(days=recheck_days)).isoformat()}
    return health['terminal']


def apply_result(version, result, recheck_days=DEFAULT_TERMINAL_RECHECK_DAYS):
    checked = result['checked_at']
    health = version.setdefault('validation', {})
    attempt = {key: deepcopy(result[key]) for key in (
        'checked_at', 'validator_version', 'mode', 'status', 'kind', 'error',
        'response_ms', 'source_id', 'revision', 'stages', 'sample',
    ) if key in result}
    version.setdefault('attempts', []).append(attempt)
    version['attempts'] = version['attempts'][-20:]
    if result.get('kind') in ENVIRONMENT_FAILURES:
        health['next_attempt_at'] = (parse_time(checked) + timedelta(hours=1)).isoformat()
        return
    health.pop('next_attempt_at', None)
    prior = deepcopy(health)
    if health.get('next_check_at') and 'next_deep_check_at' not in health:
        health['next_deep_check_at'] = health['next_check_at']
    health.update({key: deepcopy(result[key]) for key in ('status', 'kind', 'error', 'response_ms', 'validator_version', 'runtime_fingerprint', 'stages', 'sample', 'engine') if key in result})
    health['last_check_at'] = checked
    if result.get('mode') == 'deep':
        health['deep_check'] = {key: deepcopy(result.get(key)) for key in (
            'checked_at', 'validator_version', 'runtime_fingerprint', 'status', 'kind')}
    if result.get('status') == 'valid':
        if result.get('mode') == 'light' and (
                prior.get('validator_version') != result.get('validator_version')
                or prior.get('status') != 'valid'):
            version['validation'] = {**prior, 'last_light_success_at': checked}
            return
        health.pop('error', None)
        health['last_success_at'] = checked
        health['consecutive_failures'] = 0
        if result.get('mode') == 'deep':
            health['last_deep_success_at'] = checked
            health['reading_books'] = deepcopy(result.get('books', []))
        if result.get('book'):
            health['book'] = result['book']
        delay = timedelta(days=1)
        health.pop('terminal', None)
    else:
        health['consecutive_failures'] = health.get('consecutive_failures', 0) + 1
        if result['status'] == 'transient':
            step = min(health['consecutive_failures'] - 1, len(COOL_DOWN_HOURS) - 1)
            delay = timedelta(hours=COOL_DOWN_HOURS[step])
        else:
            delay = timedelta(days=7)
    delay = max(delay, timedelta(seconds=result.get('retry_after', 0)))
    health['next_check_at'] = (parse_time(checked) + delay).isoformat()
    # A successful search must not postpone the next complete reading check.
    if result.get('mode') == 'deep' or result.get('status') != 'valid':
        health['next_deep_check_at'] = health['next_check_at']
    audit = result.get('audit', {})
    if audit.get('complete') or audit.get('decision') in ('block', 'review'):
        version['audit'] = audit
    version.setdefault('engine_results', {})[result['validator_version']] = deepcopy(health)
    if result.get('status') != 'valid':
        # Park a revision whose attempts already prove this engine cannot read it.
        # A running worker keeps its answers; only a future re-check can revive it.
        park(version, recheck_days=recheck_days)


def eligibility(version, policy_version, now=None, max_age_days=7, grace_hours=72):
    now = now or datetime.now(timezone.utc)
    audit, health = version.get('audit', {}), version.get('validation', {})
    if audit.get('decision') != 'pass' or not audit.get('complete') or audit.get('policy_version') != policy_version:
        return None
    if health.get('validator_version') != VALIDATOR_VERSION:
        return None
    if health.get('runtime_fingerprint') != runtime_fingerprint():
        return None
    sample = health.get('sample', {})
    # One readable book with two distinct chapters is what a reader needs; a second
    # book only raises the confidence badge and is not an admission requirement.
    if sample.get('books', 0) < 1 or sample.get('chapters', 0) < 2:
        return None
    for value in (audit.get('checked_at'), health.get('last_deep_success_at')):
        stamp = parse_time(value)
        if not stamp or stamp > now + timedelta(minutes=5) or now - stamp > timedelta(days=max_age_days):
            return None
    if health.get('status') == 'valid':
        return 'ready'
    last_success = parse_time(health.get('last_deep_success_at'))
    if (health.get('status') == 'transient' and health.get('consecutive_failures', 0) < 3 and last_success
            and timedelta(0) <= now - last_success <= timedelta(hours=grace_hours)):
        return 'grace'
    return None
