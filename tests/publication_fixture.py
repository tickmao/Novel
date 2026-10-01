"""Small release thresholds and synthetic runtime evidence for unit tests only."""

import json
from runtime_config import ENGINE_COMMIT, VALIDATOR_VERSION, runtime_fingerprint


def enable_test_publication(root):
    config = root / 'config/supplement_config.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    value = json.loads(config.read_text()) if config.exists() else {}
    value['publication'] = {'initial_healthy_min': 0, 'initial_reserve_min': 0}
    config.write_text(json.dumps(value))
    evidence = root / 'reports/runtime/compatibility.json'
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({
        'status': 'passed', 'engine_commit': ENGINE_COMMIT, 'validator_version': VALIDATOR_VERSION,
        'runtime_fingerprint': runtime_fingerprint(),
        'fixture_count': 6, 'fixture_failures': 0, 'live_sample_count': 60,
        'container_digest': 'sha256:' + '0' * 64,
    }))
