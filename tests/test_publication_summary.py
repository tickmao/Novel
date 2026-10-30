import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from check_publication import SUMMARY_LIMIT, _targets, summary_text


def manifest(verified=140, extended=1000, unverified=753):
    return {
        'status': 'degraded',
        'release_id': '2b4ddb1400b301dd',
        'published_at': '2026-10-28T15:36:38.719897+00:00',
        'counts': {'verified': verified, 'extended': extended,
                   'unverified': unverified, 'legacy': 0},
    }


def large_report(rows=2000):
    """A report shaped like a real run: the raw payload grows without bound."""
    return {
        'mode': 'catchup',
        'finished_at': '2026-10-28T15:35:35.522865+00:00',
        'published': True,
        'dry_run': False,
        'deep_checked': rows,
        'deep': {'valid': 4, 'unverified': rows - 4},
        'deep_kinds': {'rule_error': 681, 'search_empty': 205, 'timeout': 94},
        'collection_decision': {'allowed': False, 'reason': 'raw_inventory_pending'},
        'raw_progress': {'raw_sources': 17161, 'eligible_revisions': 12650,
                         'attempted_revisions': 3023, 'pending_revisions': 9630},
        'inventory': {'reserve_count': 18, 'healthy_count': 138},
        'deep_examples': {f'kind_{index}': {'name': f'站点{index}', 'error': '上游返回 HTTP 401'}
                          for index in range(15)},
        'deep_results': [{'source_id': f'id{index}', 'revision': f'rev{index}',
                          'result': {'kind': 'rule_error', 'status': 'unverified',
                                     'detail': 'x' * 120}} for index in range(rows)],
    }


class SummaryTests(unittest.TestCase):
    def test_a_large_report_stays_within_the_step_limit(self):
        text = summary_text(manifest(), large_report())

        self.assertLessEqual(len(text.encode()), SUMMARY_LIMIT)
        self.assertIn('## Publication', text)
        self.assertIn('## Maintenance run', text)
        self.assertIn('verified: 140 / 950', text)
        self.assertIn('pending 9630', text)
        self.assertNotIn('deep_results', text)

    def test_a_small_report_is_not_truncated(self):
        report = large_report(rows=2)

        text = summary_text(manifest(), report)

        self.assertNotIn('truncated', text)
        self.assertIn('failure kinds: rule_error=681', text)
        self.assertIn('## Incidents', text)
        self.assertIn('- none', text)

    def test_a_missing_report_still_renders(self):
        text = summary_text(manifest(), {})

        self.assertIn('verified: 140 / 950', text)
        self.assertIn('deep attempts: 0, valid: 0', text)
        self.assertIn('finished: unknown', text)
        self.assertIn('- none', text)

    def test_oversized_output_is_cut_and_marked(self):
        text = summary_text(manifest(), large_report(), limit=200)

        self.assertLessEqual(len(text.encode()), 200)
        self.assertIn('truncated', text)
        self.assertIn('## Publication', text)

    def test_incidents_are_named(self):
        report = large_report(rows=2)
        report['engine_incident'] = {'reason': 'image mismatch'}

        text = summary_text(manifest(), report)

        self.assertIn('## Incidents\n\n- engine_incident', text)

    def test_targets_come_from_config_and_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(_targets(root), (950, 1000))

            config = root / 'config' / 'supplement_config.json'
            config.parent.mkdir(parents=True)
            config.write_text('{"inventory": {"verified_target": 900, "extended_target": 800}}')
            self.assertEqual(_targets(root), (900, 800))


if __name__ == '__main__':
    unittest.main()
