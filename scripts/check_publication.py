"""Verify a release without contacting source websites."""
import argparse
import hashlib
import json
from pathlib import Path

from source_store import read_json


def check(root, allow_bootstrap=False, current_health=False):
    base = root / 'sources/legado'
    manifest = read_json(base / 'main/publication.json')
    if not manifest:
        if allow_bootstrap:
            return {'status': 'bootstrap', 'count': len(read_json(base / 'full.json', [])), 'message': 'No verified release yet'}
        raise ValueError('Publication manifest is missing')
    public = (base / 'full.json').read_bytes()
    if public != (base / 'main/full.json').read_bytes():
        raise ValueError('Public source mirrors differ')
    if hashlib.sha256(public).hexdigest() != manifest['sha256']:
        raise ValueError('Publication checksum mismatch')
    sources = json.loads(public)
    if len(sources) != manifest['count'] or len({item['bookSourceUrl'] for item in sources}) != len(sources):
        raise ValueError('Publication count or identities are inconsistent')
    if read_json(root / 'docs/publication.json') != manifest:
        raise ValueError('Website publication manifest differs')
    if manifest.get('schema_version') == 3:
        if current_health:
            from source_inventory import SourceInventory
            from source_health import eligibility
            inventory = SourceInventory(root)
            for ref in manifest['sources']:
                version = inventory.store.get(ref['source_id'])['versions'][ref['revision']]
                if eligibility(version, inventory.policy.auditor.version) is None:
                    raise ValueError('A published source lacks current reading or content evidence')
        if manifest.get('healthy_count', 0) + manifest.get('grace_count', 0) != len(sources):
            raise ValueError('Publication health counts differ')
    readme = (root / 'README.md').read_text()
    index = (root / 'docs/index.html').read_text()
    count = manifest['count']
    if f'全量书源 ({count} 个)' not in readme or f'{count} 个纯小说书源' not in index:
        raise ValueError('Visible publication counts differ')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--allow-bootstrap', action='store_true')
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--current-health', action='store_true')
    args = parser.parse_args()
    result = check(args.base_dir, args.allow_bootstrap, args.current_health)
    if args.summary:
        print(f'Published sources: {result["count"]}\n\nStatus: {result["status"]}')
        report = read_json(args.base_dir / 'sources/legado/main/maintenance_report.json', {})
        print('\n```json\n' + json.dumps(report, ensure_ascii=False, indent=2) + '\n```')
    else:
        print(json.dumps({key: value for key, value in result.items() if key != 'sources'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
