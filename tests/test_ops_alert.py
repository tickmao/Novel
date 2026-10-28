import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from ops_alert import abnormal, body_of, sync_alert


class Result:
    def __init__(self, stdout=''):
        self.stdout = stdout
        self.returncode = 0


class Runner:
    def __init__(self, open_issues=None):
        self.calls = []
        self.open_issues = open_issues or []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs.get('input')))
        if argv[:3] == ['gh', 'issue', 'list']:
            return Result(json.dumps(self.open_issues))
        if argv[:3] == ['gh', 'issue', 'create']:
            return Result('https://github.com/tickmao/Novel/issues/7')
        return Result('')


def summary(verified=137, extended=1000, unverified=800, incidents=()):
    return {'verified': verified, 'extended': extended, 'unverified': unverified,
            'verified_share': round(verified / extended, 4) if extended else 0.0,
            'attempts': 1500, 'valid': 150, 'conversion': 0.1,
            'top_kinds': [('rule_error', 300)], 'incidents': list(incidents),
            'status': 'degraded', 'published_at': '2026-10-28T00:00:00+00:00',
            'checked_at': '2026-10-28T00:00:00+00:00'}


class AlertTests(unittest.TestCase):
    def test_verified_shortfall_keeps_the_issue_open(self):
        self.assertTrue(abnormal(summary()))
        runner = Runner()
        result = sync_alert(summary(), repo='tickmao/Novel', runner=runner)
        self.assertEqual(result['action'], 'created')
        self.assertIn('verified **137** / 950', runner.calls[-1][1])

    def test_healthy_delivery_closes_the_issue(self):
        runner = Runner(open_issues=[{'number': 7}])
        result = sync_alert(summary(verified=960), repo='tickmao/Novel', runner=runner)
        self.assertEqual(result['action'], 'closed')
        self.assertEqual(runner.calls[-1][0][:4], ['gh', 'issue', 'close', '7'])

    def test_existing_issue_gets_a_comment_not_a_duplicate(self):
        runner = Runner(open_issues=[{'number': 7}])
        result = sync_alert(summary(incidents=['network_incident']), repo='tickmao/Novel', runner=runner)
        self.assertEqual(result['action'], 'commented')
        self.assertNotIn('create', [call[0][2] for call in runner.calls])

    def test_incident_alone_is_abnormal(self):
        self.assertTrue(abnormal(summary(verified=960, incidents=['engine_incident'])))
        self.assertFalse(abnormal(summary(verified=960)))

    def test_body_reports_both_targets(self):
        text = body_of(summary(), verified_target=950, extended_target=1000)
        self.assertIn('verified **137** / 950', text)
        self.assertIn('extended **1000** / 1000', text)


if __name__ == '__main__':
    unittest.main()
