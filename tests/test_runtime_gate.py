import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from probe_runtime import ENGINE_FAILURE_SHARE, compatibility_report
from runtime_config import ENGINE_COMMIT, VALIDATOR_VERSION, runtime_fingerprint

DIGEST = 'sha256:' + 'a' * 64


def row(kind='reading', status='valid'):
    return {'result': {'kind': kind, 'status': status}}


def fixture(**overrides):
    base = {'engine_commit': ENGINE_COMMIT, 'validator_version': VALIDATOR_VERSION,
            'runtime_fingerprint': runtime_fingerprint(), 'container_digest': DIGEST,
            'fixture_count': 6, 'fixture_failures': 0}
    return {**base, **overrides}


def sample(rows, **overrides):
    base = {'engine_commit': ENGINE_COMMIT, 'runtime_fingerprint': runtime_fingerprint(),
            'container_digest': DIGEST, 'live_sample_count': len(rows), 'rows': rows}
    return {**base, **overrides}


def healthy_rows(count=60):
    return [row() for _ in range(count)]


class CompatibilityReportTests(unittest.TestCase):
    def test_a_healthy_sample_passes(self):
        report = compatibility_report(fixture(), sample(healthy_rows()), DIGEST)

        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['live_sample_count'], 60)
        self.assertEqual(report['live_valid_count'], 60)

    def test_one_engine_hiccup_does_not_block_the_runtime(self):
        rows = healthy_rows(59) + [row('engine_timeout', 'transient')]

        report = compatibility_report(fixture(), sample(rows), DIGEST)

        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['engine_failure_share'], round(1 / 60, 4))

    def test_engine_failures_at_the_threshold_block_the_runtime(self):
        failures = int(60 * ENGINE_FAILURE_SHARE)
        rows = healthy_rows(60 - failures) + [row('engine_crash', 'transient')] * failures

        report = compatibility_report(fixture(), sample(rows), DIGEST)

        self.assertEqual(report['status'], 'blocked')

    def test_a_short_sample_blocks_the_runtime(self):
        report = compatibility_report(fixture(), sample(healthy_rows(59)), DIGEST)

        self.assertEqual(report['status'], 'blocked')

    def test_a_sample_without_a_single_success_blocks_the_runtime(self):
        rows = [row('search_empty', 'unverified') for _ in range(60)]

        report = compatibility_report(fixture(), sample(rows), DIGEST)

        self.assertEqual(report['status'], 'blocked')

    def test_a_container_digest_mismatch_blocks_the_runtime(self):
        report = compatibility_report(fixture(), sample(healthy_rows()), 'sha256:' + 'b' * 64)

        self.assertEqual(report['status'], 'blocked')

    def test_a_failing_fixture_blocks_the_runtime(self):
        report = compatibility_report(fixture(fixture_failures=1), sample(healthy_rows()), DIGEST)

        self.assertEqual(report['status'], 'blocked')

    def test_a_stale_fingerprint_blocks_the_runtime(self):
        report = compatibility_report(fixture(runtime_fingerprint='stale'), sample(healthy_rows()), DIGEST)

        self.assertEqual(report['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
