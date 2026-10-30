import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import runtime_config
from runtime_config import (PROBE_PROTOCOL, SEMANTIC_FILES, VALIDATOR_VERSION,
                            fingerprint_inputs, runtime_fingerprint)
from source_store import digest

HARNESS_FILES = ('scripts/runtime_validator.py', 'scripts/probe_runtime.py', 'runtime/Dockerfile')


def write_semantic_files(root, text):
    for name in SEMANTIC_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


class EvidenceIdentityTests(unittest.TestCase):
    def test_only_the_reading_semantics_are_evidence_inputs(self):
        self.assertEqual(set(SEMANTIC_FILES),
                         {'runtime/NovelProbe.kt', 'runtime/legado-compatibility.patch'})
        for name in HARNESS_FILES:
            self.assertNotIn(name, SEMANTIC_FILES)

    def test_inputs_cover_the_engine_and_the_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_semantic_files(root, 'probe')
            inputs = fingerprint_inputs(root)

        self.assertEqual(set(inputs), {'engine', 'protocol', *SEMANTIC_FILES})
        self.assertEqual(inputs['protocol'], PROBE_PROTOCOL)
        self.assertEqual(inputs['engine'], runtime_config.ENGINE_COMMIT)

    def test_the_fingerprint_is_stable(self):
        self.assertEqual(runtime_fingerprint(), runtime_fingerprint())
        self.assertEqual(runtime_fingerprint(), digest(fingerprint_inputs()))

    def test_a_harness_edit_cannot_reset_the_evidence(self):
        """Editing the validator must not change the identity that gates evidence."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_semantic_files(root, 'probe')
            before = digest(fingerprint_inputs(root))
            (root / 'scripts').mkdir(parents=True, exist_ok=True)
            (root / 'scripts/runtime_validator.py').write_text('# rewritten harness\n')
            (root / 'runtime/Dockerfile').write_text('FROM scratch\n')
            after = digest(fingerprint_inputs(root))

        self.assertEqual(before, after)

    def test_a_probe_change_does_reset_the_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_semantic_files(root, 'probe')
            before = digest(fingerprint_inputs(root))
            (root / 'runtime/NovelProbe.kt').write_text('probe, revised\n')
            after = digest(fingerprint_inputs(root))

        self.assertNotEqual(before, after)

    def test_raising_the_probe_protocol_changes_the_fingerprint(self):
        baseline = digest(fingerprint_inputs())
        with patch.object(runtime_config, 'PROBE_PROTOCOL', PROBE_PROTOCOL + 1):
            self.assertNotEqual(digest(fingerprint_inputs()), baseline)

    def test_the_version_names_the_admission_protocol(self):
        self.assertTrue(VALIDATOR_VERSION.startswith('legado-reading-'))
        self.assertTrue(VALIDATOR_VERSION.endswith(runtime_config.ENGINE_COMMIT[:12]))


if __name__ == '__main__':
    unittest.main()
