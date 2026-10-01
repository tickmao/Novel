"""Validation transitions and publication eligibility."""

from datetime import datetime, timedelta, timezone
from copy import deepcopy

from source_store import parse_time, utcnow
from runtime_config import ENVIRONMENT_FAILURES, VALIDATOR_VERSION, runtime_fingerprint


def apply_result(version, result):
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
    else:
        health['consecutive_failures'] = health.get('consecutive_failures', 0) + 1
        if result['status'] == 'transient':
            delay = timedelta(hours=(1, 6, 24)[min(health['consecutive_failures'] - 1, 2)])
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
    if sample.get('books', 0) < 2 or sample.get('chapters', 0) < 4:
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
