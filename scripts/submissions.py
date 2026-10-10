"""Check submission envelopes and archive accepted inputs for normal validation."""

import argparse
import json
import time
from pathlib import Path
from reading_validator import public_url
from source_collector import parse_sources
from source_store import digest, read_json, utcnow


def submission_input(value):
    if isinstance(value, list) or isinstance(value, dict) and 'bookSourceUrl' in value:
        sources = parse_sources(value)
        if not sources:
            raise ValueError('Submission has no source definitions')
        return sources, None
    if not isinstance(value, dict):
        raise ValueError('Submission must be a source array or an envelope')
    if value.get('content') is not None:
        sources = parse_sources(value['content'])
        if not sources:
            raise ValueError('Submission content has no source definitions')
        return sources, None
    url = value.get('url')
    if not isinstance(url, str):
        raise ValueError('Submission requires source content or a public URL')
    public_url(url)
    return [], url


async def collect_submissions(store, client, root, save=True, deadline=None):
    states = store.state.setdefault('submissions', {})
    report = {'files': 0, 'sources': 0, 'errors': []}
    for path in sorted((root / 'sources/legado/submissions').glob('*.json')):
        if deadline is not None and time.monotonic() >= deadline:
            break
        identity = digest(path.read_text())
        previous = states.get(path.name, {})
        if previous.get('sha256') == identity:
            continue
        try:
            sources, url = submission_input(read_json(path))
            if url:
                response = await client.fetch({'url': url})
                sources = parse_sources(json.loads(response.text))
            if not sources:
                raise ValueError('Submission URL returned no source definitions')
            for source in sources:
                store.ingest(source, {'provider': 'submission', 'file': path.name, 'url': url})
            states[path.name] = {'sha256': identity, 'archived_at': utcnow()}
            report['files'] += 1
            report['sources'] += len(sources)
            if save:
                store.save()
        except Exception as exc:
            report['errors'].append({'file': path.name, 'error': str(exc)[:150]})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--check', type=Path, nargs='+')
    inputs.add_argument('--directory', type=Path)
    args = parser.parse_args()
    for path in args.check or sorted(args.directory.glob('*.json')):
        submission_input(read_json(path))
        print(f'{path.name}: format accepted; reading and content review are pending')


if __name__ == '__main__':
    main()
