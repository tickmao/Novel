import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from reading_validator import ProbeFailure
from runtime_validator import (TRANSIENT_KINDS, RuntimeReadingValidator, classify_engine_error)
from source_policy import SourcePolicy


def source(**fields):
    return {'bookSourceName': 'Fixture', 'bookSourceUrl': 'https://books.example', 'bookSourceType': 0,
            'searchUrl': '/search?q={{key}}', 'ruleSearch': {}, 'ruleToc': {}, 'ruleContent': {}, **fields}


class Session:
    def __init__(self, failure=None):
        self.failure = failure
        self.started = True

    async def __aenter__(self):
        if self.failure:
            raise self.failure
        return self

    async def __aexit__(self, *_):
        return False


class ClassificationTests(unittest.TestCase):
    def test_engine_messages_keep_their_real_cause(self):
        cases = {
            '该书源未配置 searchUrl': 'no_search_rule',
            '(certificate_unknown) PKIX path building failed': 'tls_error',
            '(certificate_expired) PKIX path validation failed': 'tls_error',
            '(internal_error) Received fatal alert: internal_error': 'tls_error',
            'Received RST_STREAM: Protocol error': 'tls_error',
            'HTTP/1.1 header parser received no bytes': 'network',
            'java.net.UnknownHostException: www.example.com': 'network',
            'HTTP 429': 'rate_limit',
            'HTTP 403': 'http_forbidden',
            'HTTP 401': 'auth_required',
            'HTTP 404': 'http_error',
            'HTTP 503': 'server_error',
            'HTTP connect timed out': 'timeout',
            'WebView rules are not supported': 'unsupported',
            'expected a list but received an object': 'rule_error',
        }
        for message, expected in cases.items():
            self.assertEqual(classify_engine_error(message), expected, message)

    def test_tls_failures_are_retried_instead_of_being_written_off(self):
        self.assertIn('tls_error', TRANSIENT_KINDS)


class PrecheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy = SourcePolicy(ROOT)

    async def test_explore_only_sources_never_start_the_engine(self):
        started = []

        def factory(payload):
            started.append(payload)
            return Session()

        validator = RuntimeReadingValidator(self.policy, session_factory=factory)
        result = await validator.probe(source(searchUrl=''))
        self.assertEqual((result['status'], result['kind']), ('unsupported', 'no_search_rule'))
        self.assertEqual(started, [])

    async def test_a_transport_failure_is_reported_as_transient(self):
        validator = RuntimeReadingValidator(
            self.policy, session_factory=lambda payload: Session(ProbeFailure('tls_error', 'handshake failed')))
        result = await validator.probe(source())
        self.assertEqual((result['status'], result['kind']), ('transient', 'tls_error'))


if __name__ == '__main__':
    unittest.main()
