"""Export a static review report. This command does not publish sources."""
import argparse
from pathlib import Path
from source_inventory import SourceInventory
from source_store import atomic_bundle, json_bytes, read_json
from validate import assert_report_output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inventory = SourceInventory(args.base_dir)
    sources = read_json(args.input) if args.input else inventory.load_raw_sources()
    accepted, rejected, stats = inventory.policy.screen_sources(sources)
    assert_report_output(args.output)
    atomic_bundle({args.output: json_bytes({'accepted': accepted, 'rejected': rejected, 'stats': stats})})


if __name__ == '__main__':
    main()
