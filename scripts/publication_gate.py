"""Keep an experimental or unverified inventory out of public releases."""

import re
from runtime_config import ENGINE_COMMIT, VALIDATOR_VERSION, runtime_fingerprint
from source_store import read_json


def compatibility_evidence(root):
    """Engine identity plus fixtures. Live reading is reported separately."""
    evidence = read_json(root / 'reports/runtime/compatibility.json', {})
    identity = (
        evidence.get('engine_commit') == ENGINE_COMMIT
        and evidence.get('validator_version') == VALIDATOR_VERSION
        and evidence.get('runtime_fingerprint') == runtime_fingerprint()
        and evidence.get('fixture_count', 0) >= 6
        and evidence.get('fixture_failures') == 0
    )
    live = (
        evidence.get('status') == 'passed'
        and evidence.get('live_sample_count', 0) >= 60
        and evidence.get('live_valid_count', 0) >= 1
        and re.fullmatch(r'sha256:[0-9a-f]{64}', str(evidence.get('container_digest', ''))) is not None
    )
    return {
        'fixtures': identity, 'live': live, 'passed': identity and live,
        'status': evidence.get('status'),
        'engine_commit': evidence.get('engine_commit'),
        'validator_version': evidence.get('validator_version'),
        'runtime_fingerprint': evidence.get('runtime_fingerprint'),
        'container_digest': evidence.get('container_digest'),
    }


def publication_gate(root, inventory, previous=None, *, extended=False):
    config = read_json(root / 'config/supplement_config.json', {}).get('publication', {})
    evidence = compatibility_evidence(root)
    launched = bool(previous and previous.get('schema_version') in (3, 4)
                    and previous.get('validator_version') == VALIDATOR_VERSION
                    and previous.get('runtime_fingerprint') == runtime_fingerprint())
    missing = []
    if extended:
        # The extended package labels unverified sources instead of claiming they
        # are readable, so it needs a working engine build, not live evidence.
        if not evidence['fixtures']:
            missing.append('engine_compatibility')
        if inventory.get('extended_count', 0) < int(config.get('extended_min', 200)):
            missing.append('extended_count')
    else:
        if not evidence['passed']:
            missing.append('engine_compatibility')
        if not launched:
            if inventory.get('healthy_count', 0) < int(config.get('initial_healthy_min', 950)):
                missing.append('healthy_count')
            if inventory.get('reserve_count', 0) < int(config.get('initial_reserve_min', 300)):
                missing.append('reserve_count')
    return {'ready': not missing, 'missing': missing, 'launched': launched, 'extended': extended,
            'runtime_status': evidence['status'], 'engine_commit': ENGINE_COMMIT,
            'validator_version': VALIDATOR_VERSION}
