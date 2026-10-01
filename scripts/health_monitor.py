"""Report verified inventory health without changing it."""
import argparse
import json
from pathlib import Path
from source_inventory import SourceInventory
from source_store import atomic_bundle, json_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = SourceInventory(args.base_dir).inventory_status()
    if args.report:
        atomic_bundle({args.report: json_bytes(report)})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
