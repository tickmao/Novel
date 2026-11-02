"""Run repeatable engine fixtures and a bounded, read-only inventory sample."""

import argparse
import asyncio
import json
import os
import time
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

from runtime_config import ENGINE_COMMIT, VALIDATOR_VERSION, runtime_fingerprint
from runtime_validator import EngineSession, RuntimeReadingValidator
from source_inventory import SourceInventory
from source_health import TRANSPORT_FAILURES
from source_store import atomic_bundle, digest, json_bytes, read_json, utcnow

# The winner is decided before the numbers arrive: a benchmark that picks its own
# rule afterwards only justifies the configuration that was already running.
BENCH_BASELINE_CONCURRENCY = 4
BENCH_P95_LIMIT_MS = 60_000
BENCH_THROUGHPUT_GAIN = 1.5


def fixtures():
    base = 'https://fixture.example'
    source = {
        'bookSourceName': 'Fixture Books', 'bookSourceUrl': base, 'bookSourceType': 0,
        'searchUrl': base + '/search?q={{key}}',
        'ruleSearch': {'bookList': '.book', 'name': 'a@text', 'bookUrl': 'a@href'},
        'ruleBookInfo': {'name': 'h1@text', 'tocUrl': '.toc@href'},
        'ruleToc': {'chapterList': '.chapter', 'chapterName': 'a@text', 'chapterUrl': 'a@href'},
        'ruleContent': {'content': '#content@text'},
    }
    responses = {base + '/search?q=Example': ''.join(
        f'<div class="book"><a href="/book/{b}">Example {b}</a></div>' for b in range(2))}
    prose = ' A traveler crossed the ancient mountain and found a quiet village near the river. ' * 10
    for b in range(2):
        responses[f'{base}/book/{b}'] = f'<h1>Example {b}</h1><a class="toc" href="/book/{b}/toc">Contents</a>'
        responses[f'{base}/book/{b}/toc'] = ''.join(
            f'<div class="chapter"><a href="/book/{b}/chapter/{c}">Chapter {c}</a></div>' for c in range(4))
        for c in range(4):
            responses[f'{base}/book/{b}/chapter/{c}'] = f'<div id="content">Book {b} chapter {c}.{prose}</div>'
    cases = [('css', deepcopy(source), deepcopy(responses))]
    options = deepcopy(source)
    options['searchUrl'] += ', {"method":"GET","charset":"UTF-8"}'
    cases.append(('spaced_request_options', options, deepcopy(responses)))
    legacy = deepcopy(source)
    legacy['ruleSearch']['bookList'] = 'class.book'
    legacy['ruleToc']['chapterList'] = 'class.chapter'
    cases.append(('legacy_selectors', legacy, deepcopy(responses)))
    javascript = deepcopy(source)
    javascript['ruleContent']['content'] += '@js:result + " Finished."'
    cases.append(('javascript_content', javascript, deepcopy(responses)))
    helper = deepcopy(source)
    helper['ruleContent']['content'] += '@js:result + java.base64DecodeToString("IEZpbmlzaGVkLg==")'
    cases.append(('java_helper', helper, deepcopy(responses)))
    dynamic = deepcopy(source)
    dynamic['searchUrl'] = '@js:"https://fixture.example/search?q=" + key'
    cases.append(('dynamic_search', dynamic, deepcopy(responses)))
    discovery = deepcopy(source)
    discovery['exploreUrl'] = '@js:JSON.stringify([{title:"History",url:"/history"}])'
    cases.append(('dynamic_discovery', discovery, deepcopy(responses)))
    adult = deepcopy(source)
    adult['exploreUrl'] = '@js:JSON.stringify([{title:"R18",url:"/adult"}])'
    cases.append(('adult_discovery', adult, deepcopy(responses)))
    js_source = deepcopy(source)
    js_source.update(ruleSearch={'bookList': '$.books', 'name': '$.name', 'bookUrl': '$.url'},
                     ruleBookInfo={'name': '$.name', 'tocUrl': '$.toc'},
                     ruleToc={'chapterList': '$.chapters', 'chapterName': '$.name', 'chapterUrl': '$.url'},
                     ruleContent={'content': '$.content'})
    pages = {base + '/search?q=Example': json.dumps({'books': [
        {'name': f'Example {b}', 'url': f'{base}/book/{b}'} for b in range(2)]})}
    for b in range(2):
        pages[f'{base}/book/{b}'] = json.dumps({'name': f'Example {b}', 'toc': f'{base}/book/{b}/toc'})
        pages[f'{base}/book/{b}/toc'] = json.dumps({'chapters': [
            {'name': f'Chapter {c}', 'url': f'{base}/book/{b}/chapter/{c}'} for c in range(4)]})
        for c in range(4):
            pages[f'{base}/book/{b}/chapter/{c}'] = json.dumps({'content': f'Book {b} chapter {c}.' + prose})
    cases.append(('jsonpath', js_source, pages))
    metadata = deepcopy(js_source)
    metadata['ruleSearch'].update(kind='$.id', bookUrl=base + '/book/{{book.kind}}')
    metadata['ruleBookInfo'].update(kind='$.id', tocUrl=base + '/book/{{book.kind}}/toc')
    metadata['ruleToc']['chapterUrl'] = '$.url@js:result + "?book=" + book.kind'
    metadata['ruleContent']['content'] = '$.content@js:result + " " + book.name'
    metadata_pages = deepcopy(pages)
    metadata_pages[base + '/search?q=Example'] = json.dumps({'books': [
        {'name': f'Example {b}', 'id': str(b)} for b in range(2)]})
    for b in range(2):
        metadata_pages[f'{base}/book/{b}'] = json.dumps({'name': f'Example {b}', 'id': str(b)})
        for c in range(4):
            metadata_pages[f'{base}/book/{b}/chapter/{c}?book={b}'] = pages[f'{base}/book/{b}/chapter/{c}']
    cases.append(('book_metadata_url', metadata, metadata_pages))
    fallback = deepcopy(source)
    fallback['exploreUrl'] = '@js:JSON.stringify([{title:"History",url:"/history"}])'
    fallback_pages = deepcopy(responses)
    fallback_pages[base + '/search?q=Example'] = '<html><body></body></html>'
    fallback_pages[base + '/search?q=History'] = ''.join(
        f'<div class="book"><a href="/book/{b}">Example {b}</a></div>' for b in range(2))
    cases.append(('discovery_title_fallback', fallback, fallback_pages))
    retry = deepcopy(source)
    retry_pages = deepcopy(responses)
    retry_pages[f'{base}/book/0/chapter/2'] = '<div id="content">short</div>'
    cases.append(('chapter_candidate_retry', retry, retry_pages))
    return cases


async def run_fixtures(root):
    inventory = SourceInventory(root)
    rows = []
    for name, source, pages in fixtures():
        validator = RuntimeReadingValidator(inventory.policy, keywords=['Example'],
                                            session_factory=lambda p: EngineSession(p, fixtures=pages))
        result = await validator.probe(source)
        passed = (result['status'] == 'blocked' and result['kind'] == 'content_audit' if name == 'adult_discovery'
                  else result['status'] == 'valid' and result.get('sample') == {'books': 2, 'chapters': 4})
        rows.append({'case': name, 'passed': passed, 'result': result})
        print(f'{name}: {result["status"]} {result.get("error", "")}', flush=True)
    return {'engine_commit': ENGINE_COMMIT, 'validator_version': VALIDATOR_VERSION,
            'runtime_fingerprint': runtime_fingerprint(), 'container_digest': os.environ.get('NOVEL_ENGINE_IMAGE'),
            'checked_at': utcnow(), 'fixture_count': len(rows),
            'fixture_failures': sum(not row['passed'] for row in rows), 'rows': rows}


def sample_refs(inventory, count):
    groups = defaultdict(list)
    for record in inventory.store.records():
        ref = (record['source_id'], record['latest_revision'])
        version = record['versions'][ref[1]]
        source = inventory.audit_payload(version)
        if inventory.policy.audit_source(source)['decision'] != 'pass':
            continue
        if source.get('enabled') is False or str(source.get('bookSourceType', 0)) != '0':
            continue
        text = json.dumps(source)
        group = ('browser' if 'startBrowser' in text or source.get('webView') else
                 'helpers' if 'java.' in text else 'js_search' if '@js:' in str(source.get('searchUrl')) else
                 'js_rules' if '@js:' in text or '<js>' in text else
                 'json' if '$.' in text else 'static')
        groups[group].append(ref)
    for group in groups.values():
        group.sort(key=lambda ref: (
            not bool(inventory.store.get(ref[0])['versions'][ref[1]].get('validation', {}).get('last_deep_success_at')),
            ref,
        ))
    selected = []
    for index in range(count):
        for group in sorted(groups):
            if index < len(groups[group]) and len(selected) < count:
                selected.append(groups[group][index])
    return selected


async def run_sample(root, count, minutes, output):
    inventory = SourceInventory(root)
    refs = sample_refs(inventory, count)
    identity = digest({'refs': refs, 'engine': VALIDATOR_VERSION, 'policy': inventory.policy.auditor.version,
                       'runtime': runtime_fingerprint(), 'container': os.environ.get('NOVEL_ENGINE_IMAGE')})
    old = read_json(output, {})
    rows = [row for row in old.get('rows', []) if not row['result']['kind'].startswith('engine_')] if old.get('input_hash') == identity else []
    done = {(r['source_id'], r['revision']) for r in rows}
    validator = RuntimeReadingValidator(inventory.policy)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + minutes * 60
    for offset in range(0, len(refs), 4):
        if loop.time() >= deadline:
            break
        batch = [ref for ref in refs[offset:offset + 4] if ref not in done]
        results = await asyncio.gather(*(validator.probe(inventory.audit_payload(
            inventory.store.get(key)['versions'][revision])) for key, revision in batch))
        for (key, revision), result in zip(batch, results):
            rows.append({'source_id': key, 'revision': revision, 'result': result})
        report = {'engine_commit': ENGINE_COMMIT, 'validator_version': VALIDATOR_VERSION,
                  'runtime_fingerprint': runtime_fingerprint(), 'container_digest': os.environ.get('NOVEL_ENGINE_IMAGE'),
                  'input_hash': identity, 'checked_at': utcnow(), 'requested': count, 'rows': rows,
                  'live_sample_count': len(rows), 'statuses': dict(Counter(r['result']['status'] for r in rows))}
        atomic_bundle({output: json_bytes(report)})
        print(f'Sampled {len(rows)}/{count}: {report["statuses"]}', flush=True)
        if results and all(r['kind'].startswith('engine_') for r in results):
            break
    return read_json(output, {'live_sample_count': 0})

def percentile(values, share):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return round(ordered[index], 1)


def summarize_round(concurrency, results, elapsed):
    attempts = len(results)
    statuses = Counter(result['status'] for result in results)
    kinds = Counter(result.get('kind') or 'unknown' for result in results)
    latencies = [float(result.get('response_ms') or 0) for result in results]
    engine = sum(count for kind, count in kinds.items() if kind.startswith('engine_'))
    transport = sum(count for kind, count in kinds.items() if kind in TRANSPORT_FAILURES)
    return {
        'concurrency': concurrency,
        'attempts': attempts,
        'valid': statuses.get('valid', 0),
        'valid_share': round(statuses.get('valid', 0) / attempts, 4) if attempts else 0.0,
        'statuses': dict(statuses),
        'kinds': dict(kinds),
        'engine_failure_share': round(engine / attempts, 4) if attempts else 0.0,
        'transport_share': round(transport / attempts, 4) if attempts else 0.0,
        'p50_ms': percentile(latencies, 0.5),
        'p95_ms': percentile(latencies, 0.95),
        'probes_per_minute': round(attempts / max(elapsed / 60, 1e-6), 2),
    }


def benchmark_decision(rounds):
    """Pick the largest concurrency that stays clean and clearly faster.

    Clean means no worker failed to start or answer, and the slowest probe still
    fits the request budget. Faster means a real gain over the baseline, because a
    benchmark that doubles contention to win 5% is a loss.
    """
    baseline = next((item for item in rounds if item['concurrency'] == BENCH_BASELINE_CONCURRENCY), None)
    if baseline is None or not baseline['attempts']:
        return {'concurrency': None, 'reason': 'no_usable_baseline'}
    accepted = [item for item in rounds
                if item['attempts'] and item['engine_failure_share'] == 0
                and item['p95_ms'] < BENCH_P95_LIMIT_MS
                and item['probes_per_minute'] >= baseline['probes_per_minute'] * BENCH_THROUGHPUT_GAIN]
    if not accepted:
        return {'concurrency': baseline['concurrency'], 'reason': 'no_round_beat_the_baseline'}
    winner = max(accepted, key=lambda item: item['concurrency'])
    return {'concurrency': winner['concurrency'], 'reason': 'highest_clean_concurrency'}


async def run_bench(root, count, concurrency_list, minutes):
    """Measure one fixed sample at several concurrencies.

    Every probe starts its own JVM in a container capped at one CPU, so twelve of
    them on four cores take turns. This writes reports only: stored evidence and
    the inventory are never touched.
    """
    inventory = SourceInventory(root)
    refs = sample_refs(inventory, count)
    payloads = [inventory.audit_payload(inventory.store.get(key)['versions'][revision])
                for key, revision in refs]
    rounds = []
    for concurrency in concurrency_list:
        started = time.monotonic()
        validator = RuntimeReadingValidator(inventory.policy, concurrency=concurrency)
        results = []
        for offset in range(0, len(payloads), concurrency):
            if time.monotonic() - started >= minutes * 60:
                break
            batch = payloads[offset:offset + concurrency]
            results.extend(await asyncio.gather(*(validator.probe(payload) for payload in batch)))
        rounds.append(summarize_round(concurrency, results, time.monotonic() - started))
        print(f'concurrency {concurrency}: {rounds[-1]["valid"]}/{rounds[-1]["attempts"]} valid,'
              f' {rounds[-1]["probes_per_minute"]}/min', flush=True)
    report = {'engine_commit': ENGINE_COMMIT, 'validator_version': VALIDATOR_VERSION,
              'runtime_fingerprint': runtime_fingerprint(),
              'container_digest': os.environ.get('NOVEL_ENGINE_IMAGE'),
              'checked_at': utcnow(), 'requested': count, 'sample_size': len(payloads),
              'rounds': rounds, 'decision': benchmark_decision(rounds)}
    atomic_bundle({root / 'reports/runtime/benchmark.json': json_bytes(report)})
    return report


# One engine hiccup in sixty samples proves nothing about the runtime. A rate
# asks the question the gate is actually there to answer: can this build read?
ENGINE_FAILURE_SHARE = 0.2


def compatibility_report(fixture, sample, container_digest):
    """Judge the pinned runtime from its fixtures and a bounded live sample."""
    rows = sample.get('rows', [])
    sampled = int(sample.get('live_sample_count', 0))
    engine_rows = sum(1 for row in rows if str(row['result'].get('kind', '')).startswith('engine_'))
    engine_share = engine_rows / sampled if sampled else 1.0
    result = {key: fixture.get(key) for key in (
        'engine_commit', 'validator_version', 'fixture_count', 'fixture_failures', 'runtime_fingerprint')}
    result.update(live_sample_count=sampled, container_digest=container_digest,
                  live_valid_count=sum(row['result'].get('status') == 'valid' for row in rows),
                  engine_failure_share=round(engine_share, 4),
                  checked_at=utcnow(), status='passed')
    if (fixture.get('engine_commit') != ENGINE_COMMIT or sample.get('engine_commit') != ENGINE_COMMIT
            or fixture.get('runtime_fingerprint') != runtime_fingerprint()
            or sample.get('runtime_fingerprint') != runtime_fingerprint()
            or not container_digest
            or result['live_valid_count'] == 0
            or fixture.get('container_digest') != container_digest
            or sample.get('container_digest') != container_digest
            or fixture.get('fixture_failures', 1) or sampled < 60
            or engine_share >= ENGINE_FAILURE_SHARE):
        result['status'] = 'blocked'
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['fixtures', 'sample', 'gate', 'bench'])
    parser.add_argument('--base-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--count', type=int, default=60)
    parser.add_argument('--max-minutes', type=float, default=20)
    parser.add_argument('--concurrency-list', default='4,8,12',
                        help='Concurrencies to compare, comma separated')
    parser.add_argument('--container-digest')
    parser.add_argument('--require-ready', action='store_true',
                        help='Fail acceptance when the runtime gate is blocked')
    args = parser.parse_args()
    output = args.base_dir / 'reports/runtime'
    if args.action == 'fixtures':
        result = await run_fixtures(args.base_dir)
        atomic_bundle({output / 'fixtures.json': json_bytes(result)})
        if result['fixture_failures']:
            raise SystemExit(1)
    elif args.action == 'sample':
        await run_sample(args.base_dir, args.count, args.max_minutes, output / 'sample.json')
    elif args.action == 'bench':
        levels = [int(item) for item in str(args.concurrency_list).split(',') if item.strip()]
        if not levels or any(level < 1 for level in levels):
            parser.error('--concurrency-list needs positive integers')
        report = await run_bench(args.base_dir, args.count, levels, args.max_minutes)
        print(json.dumps(report['decision'], indent=2))
    else:
        fixture = read_json(output / 'fixtures.json', {})
        sample = read_json(output / 'sample.json', {})
        result = compatibility_report(fixture, sample, args.container_digest)
        atomic_bundle({output / 'compatibility.json': json_bytes(result)})
        print(json.dumps(result, indent=2))
        if args.require_ready and result['status'] != 'passed':
            raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
