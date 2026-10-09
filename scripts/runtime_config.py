"""Identity of the runtime and the reading protocol used for admission."""

from functools import lru_cache

ENGINE_REPOSITORY = 'https://github.com/lukelzlz/legado-server.git'
ENGINE_COMMIT = '3cb7acb2a2892e71184c1dd12c8e8d7b5a60fbb7'
VALIDATOR_VERSION = 'legado-reading-v3:' + ENGINE_COMMIT[:12]
ENVIRONMENT_FAILURES = {'engine_unavailable', 'engine_timeout', 'engine_protocol', 'engine_crash'}


@lru_cache(maxsize=1)
def runtime_fingerprint():
    from pathlib import Path
    from source_store import digest
    root = Path(__file__).resolve().parents[1]
    return digest({name: (root / name).read_text() for name in (
        'runtime/NovelProbe.kt', 'runtime/Dockerfile', 'runtime/legado-compatibility.patch',
        'scripts/runtime_validator.py', 'scripts/probe_runtime.py')})
