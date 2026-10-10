"""Regenerate display statistics from an existing verified publication."""
import argparse
import hashlib
import json
from pathlib import Path

from safe_updater import stats_files
from source_store import atomic_bundle, read_json


class SourceUpdater:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)

    def count_legado_sources(self):
        return len(read_json(self.base_dir / 'sources/legado/full.json', []))

    def count_xsreader_sources(self):
        path = self.base_dir / 'sources/xsreader/full.xbs'
        return path.stat().st_size // (9 * 1024) if path.exists() else 0

    def count_ifreetime_sources(self):
        return 0

    def get_all_source_counts(self):
        return {'legado': self.count_legado_sources(), 'xsreader': self.count_xsreader_sources(), 'ifreetime': 0}

    def run_update(self):
        from daily_maintenance import writer_lock
        base = self.base_dir / 'sources/legado'
        with writer_lock(base):
            manifest = read_json(base / 'main/publication.json')
            if not manifest:
                raise ValueError('Create a verified publication before updating display statistics')
            public = (base / 'full.json').read_bytes()
            if public != (base / 'main/full.json').read_bytes() or hashlib.sha256(public).hexdigest() != manifest['sha256']:
                raise ValueError('Publication is inconsistent')
            atomic_bundle(stats_files(self.base_dir, manifest['count'], manifest))
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    updater = SourceUpdater(args.base_dir)
    if not args.dry_run:
        updater.run_update()
    print(json.dumps(updater.get_all_source_counts()))


if __name__ == '__main__':
    main()
