import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from probe_runtime import (BENCH_BASELINE_CONCURRENCY, BENCH_P95_LIMIT_MS, benchmark_decision,
                           percentile, summarize_round)


def round_of(concurrency, attempts=60, valid=20, engine=0, transport=40, p95=30_000, rate=None):
    return {'concurrency': concurrency, 'attempts': attempts, 'valid': valid,
            'engine_failure_share': engine, 'transport_share': transport / attempts,
            'p95_ms': p95, 'probes_per_minute': rate if rate is not None else float(concurrency)}


class BenchmarkDecisionTests(unittest.TestCase):
    def test_the_largest_clean_round_wins(self):
        rounds = [round_of(4), round_of(8, engine=0.05), round_of(12)]

        self.assertEqual(benchmark_decision(rounds), {'concurrency': 12,
                                                      'reason': 'highest_clean_concurrency'})

    def test_a_round_with_a_worker_failure_is_not_a_candidate(self):
        rounds = [round_of(4), round_of(8), round_of(12, engine=0.1)]

        self.assertEqual(benchmark_decision(rounds)['concurrency'], 8)

    def test_a_round_that_is_not_faster_is_not_a_candidate(self):
        rounds = [round_of(4, rate=2.0), round_of(8, rate=2.5), round_of(12, rate=5.0)]

        self.assertEqual(benchmark_decision(rounds)['concurrency'], 12)
        rounds = [round_of(4, rate=2.0), round_of(12, rate=2.6)]
        self.assertEqual(benchmark_decision(rounds), {'concurrency': 4,
                                                      'reason': 'no_round_beat_the_baseline'})

    def test_a_slow_round_is_not_a_candidate(self):
        rounds = [round_of(4), round_of(12, p95=BENCH_P95_LIMIT_MS + 1)]

        self.assertEqual(benchmark_decision(rounds)['concurrency'], BENCH_BASELINE_CONCURRENCY)

    def test_a_missing_baseline_means_no_decision(self):
        self.assertEqual(benchmark_decision([round_of(8)]),
                         {'concurrency': None, 'reason': 'no_usable_baseline'})


class RoundSummaryTests(unittest.TestCase):
    def test_the_round_separates_engine_and_transport_failures(self):
        results = [
            {'status': 'valid', 'kind': 'reading', 'response_ms': 1000},
            {'status': 'unverified', 'kind': 'engine_timeout', 'response_ms': 30_000},
            {'status': 'transient', 'kind': 'network', 'response_ms': 500},
            {'status': 'unverified', 'kind': 'search_empty', 'response_ms': 2000},
        ]

        summary = summarize_round(4, results, elapsed=60)

        self.assertEqual(summary['attempts'], 4)
        self.assertEqual(summary['valid'], 1)
        self.assertEqual(summary['engine_failure_share'], 0.25)
        self.assertEqual(summary['transport_share'], 0.25)
        self.assertEqual(summary['probes_per_minute'], 4.0)
        self.assertEqual(summary['p95_ms'], 30_000)

    def test_an_empty_round_has_no_shares(self):
        summary = summarize_round(4, [], elapsed=1)

        self.assertEqual(summary['attempts'], 0)
        self.assertEqual(summary['engine_failure_share'], 0.0)
        self.assertEqual(summary['p50_ms'], 0.0)

    def test_percentiles_cover_both_edges(self):
        self.assertEqual(percentile([], 0.5), 0.0)
        self.assertEqual(percentile([10, 20, 30], 0.0), 10)
        self.assertEqual(percentile([10, 20, 30], 1.0), 30)


class BenchmarkRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_benchmark_writes_only_its_report(self):
        from probe_runtime import run_bench

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'reports/runtime').mkdir(parents=True)
            inventory = MagicMock()
            inventory.audit_payload.side_effect = lambda version: version
            inventory.store.get.return_value = {'versions': {'revision': {'source': {}}}}
            measured = [round_of(4, rate=1.0), round_of(8, rate=2.0)]
            with patch('probe_runtime.SourceInventory', return_value=inventory), \
                    patch('probe_runtime.sample_refs', return_value=[('key', 'revision')]), \
                    patch('probe_runtime.RuntimeReadingValidator') as validator, \
                    patch('probe_runtime.summarize_round', side_effect=measured):
                validator.return_value.probe = AsyncMock(
                    return_value={'status': 'valid', 'kind': 'reading', 'response_ms': 900})
                report = await run_bench(root, 1, [4, 8], 1)

            self.assertEqual([row['concurrency'] for row in report['rounds']], [4, 8])
            self.assertEqual(report['decision'], {'concurrency': 8,
                                                  'reason': 'highest_clean_concurrency'})
            written = json.loads((root / 'reports/runtime/benchmark.json').read_text())
            self.assertEqual(written['sample_size'], 1)
            self.assertEqual(sorted(path.name for path in (root / 'reports/runtime').iterdir()),
                             ['benchmark.json'])


if __name__ == '__main__':
    unittest.main()
