import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from ops_alert import abnormal, body_of, sync_alert


class Result:
    def __init__(self, stdout='', returncode=0, stderr=''):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class Runner:
    def __init__(self, open_issues=None, create_returncode=0, label_returncode=0):
        self.calls = []
        self.open_issues = open_issues or []
        self.create_returncode = create_returncode
        self.label_returncode = label_returncode

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs.get('input')))
        if argv[:3] == ['gh', 'issue', 'list']:
            return Result(json.dumps(self.open_issues))
        if argv[:3] == ['gh', 'label', 'create']:
            return Result('', returncode=self.label_returncode)
        if argv[:3] == ['gh', 'issue', 'create']:
            if self.create_returncode:
                return Result('', returncode=self.create_returncode, stderr='label not found')
            return Result('https://github.com/tickmao/Novel/issues/7')
        return Result('')


def summary(verified=137, extended=1000, unverified=800, incidents=(), spinning=False,
            published_at=None):
    return {'verified': verified, 'extended': extended, 'unverified': unverified,
            'verified_share': round(verified / extended, 4) if extended else 0.0,
            'attempts': 1500, 'valid': 150, 'conversion': 0.1,
            'top_kinds': [('rule_error', 300)], 'incidents': list(incidents),
            'usable_target': 500, 'freshness': {'count': 137, 'oldest_days': 2.0, 'median_days': 0.5},
            'status': 'degraded',
            'published_at': published_at or datetime.now(timezone.utc).isoformat(),
            'checked_at': '2026-10-28T00:00:00+00:00',
            'verified_delta': 0, 'transport_share': 0.0, 'spinning': spinning,
            'week': [{'checked': 4000, 'verified': verified - 8},
                     {'checked': 4462, 'verified': verified}]}


class AlertTests(unittest.TestCase):
    def test_verified_shortfall_keeps_the_issue_open(self):
        self.assertTrue(abnormal(summary()))
        runner = Runner()
        result = sync_alert(summary(), repo='tickmao/Novel', runner=runner)
        self.assertEqual(result['action'], 'created')
        self.assertIn('usable **137** / 500', runner.calls[-1][1])
        self.assertEqual(runner.calls[-2][0][:3], ['gh', 'label', 'create'])

    def test_a_failed_issue_create_is_reported(self):
        runner = Runner(create_returncode=1)

        with self.assertRaises(SystemExit):
            sync_alert(summary(), repo='tickmao/Novel', runner=runner)

    def test_healthy_delivery_closes_the_issue(self):
        runner = Runner(open_issues=[{'number': 7}])
        result = sync_alert(summary(verified=520), repo='tickmao/Novel', runner=runner)
        self.assertEqual(result['action'], 'closed')
        self.assertEqual(runner.calls[-1][0][:4], ['gh', 'issue', 'close', '7'])

    def test_existing_issue_gets_a_comment_not_a_duplicate(self):
        runner = Runner(open_issues=[{'number': 7}])
        result = sync_alert(summary(incidents=['network_incident']), repo='tickmao/Novel', runner=runner)
        self.assertEqual(result['action'], 'commented')
        self.assertNotIn('create', [call[0][2] for call in runner.calls])

    def test_incident_alone_is_abnormal(self):
        self.assertTrue(abnormal(summary(verified=520, incidents=['engine_incident'])))
        self.assertFalse(abnormal(summary(verified=520)))

    def test_body_reports_the_usable_target_and_the_evidence_age(self):
        text = body_of(summary(), extended_target=1000)
        self.assertIn('usable **137** / 500', text)
        self.assertIn('evidence age: oldest 2.0d', text)
        self.assertIn('spinning: no', text)

    def test_the_target_is_an_absolute_count_of_usable_sources(self):
        self.assertTrue(abnormal(summary(verified=137)))
        self.assertTrue(abnormal(summary(verified=499)))
        self.assertFalse(abnormal(summary(verified=500)))

    def test_spinning_opens_the_issue(self):
        self.assertTrue(abnormal(summary(verified=500, spinning=True)))

    def test_a_stale_publication_opens_the_issue(self):
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        self.assertTrue(abnormal(summary(verified=500, published_at=old)))

    def test_stale_evidence_opens_the_issue(self):
        stale = summary(verified=500)
        stale['freshness'] = {'count': 500, 'oldest_days': 9.0, 'median_days': 4.0}

        self.assertTrue(abnormal(stale))


if __name__ == '__main__':
    unittest.main()
