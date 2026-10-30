"""Identity of the runtime and the reading protocol used for admission."""

from functools import lru_cache

ENGINE_REPOSITORY = 'https://github.com/lukelzlz/legado-server.git'
ENGINE_COMMIT = '3cb7acb2a2892e71184c1dd12c8e8d7b5a60fbb7'
# Only a change to what counts as a readable source may raise this: the admission
# profile (books, chapters, characters), the probe protocol, or the engine commit.
# Reclassifying errors, moving code or rewording messages must not invalidate the
# stored evidence of thousands of sources.
VALIDATOR_VERSION = 'legado-reading-v5:' + ENGINE_COMMIT[:12]
PROBE_PROTOCOL = 1
ENVIRONMENT_FAILURES = {'engine_unavailable', 'engine_timeout', 'engine_protocol', 'engine_crash'}

# The engine commit, the probe protocol, the probe implementation and the engine
# patch decide a verdict. The validator, the probe driver and the image definition
# are harness code: raise PROBE_PROTOCOL when the JVM base image changes behaviour.
SEMANTIC_FILES = ('runtime/NovelProbe.kt', 'runtime/legado-compatibility.patch')


def fingerprint_inputs(root=None):
    from pathlib import Path
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    return {'engine': ENGINE_COMMIT, 'protocol': PROBE_PROTOCOL,
            **{name: (root / name).read_text() for name in SEMANTIC_FILES}}


@lru_cache(maxsize=1)
def runtime_fingerprint():
    from source_store import digest
    return digest(fingerprint_inputs())
