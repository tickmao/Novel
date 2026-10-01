"""Keep an experimental or unverified inventory out of public releases."""

import re
from runtime_config import ENGINE_COMMIT, VALIDATOR_VERSION, runtime_fingerprint
from source_store import read_json


def publication_gate(root, inventory, previous=None):
    config = read_json(root / 'config/supplement_config.json', {}).get('publication', {})
    evidence = read_json(root / 'reports/runtime/compatibility.json', {})
    compatible = (
        evidence.get('status') == 'passed'
        and evidence.get('engine_commit') == ENGINE_COMMIT
        and evidence.get('validator_version') == VALIDATOR_VERSION
        and evidence.get('runtime_fingerprint') == runtime_fingerprint()
        and evidence.get('fixture_count', 0) >= 6
        and evidence.get('fixture_failures') == 0
        and evidence.get('live_sample_count', 0) >= 60
        and evidence.get('live_valid_count', 0) >= 1
        and re.fullmatch(r'sha256:[0-9a-f]{64}', str(evidence.get('container_digest', ''))) is not None
    )
    launched = bool(previous and previous.get('schema_version') == 3
                    and previous.get('validator_version') == VALIDATOR_VERSION
                    and previous.get('runtime_fingerprint') == runtime_fingerprint())
    missing = []
    if not compatible:
        missing.append('engine_compatibility')
    if not launched:
        if inventory.get('healthy_count', 0) < int(config.get('initial_healthy_min', 950)):
            missing.append('healthy_count')
        if inventory.get('reserve_count', 0) < int(config.get('initial_reserve_min', 300)):
            missing.append('reserve_count')
    return {'ready': not missing, 'missing': missing, 'launched': launched,
            'engine_commit': ENGINE_COMMIT, 'validator_version': VALIDATOR_VERSION}
