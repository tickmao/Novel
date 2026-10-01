"""Produce a reading validation report without updating public sources."""
import argparse
import asyncio
from pathlib import Path
from batch_validator import BatchValidator
from source_store import atomic_bundle, json_bytes, read_json, utcnow


def assert_report_output(path):
    root = Path(__file__).resolve().parents[1] / 'sources/legado'
    if path.resolve() in {(root / 'full.json').resolve(), (root / 'main/full.json').resolve()}:
        raise ValueError('Only the publisher can write public source files')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--invalid', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--timeout', type=int, default=10)
    parser.add_argument('--sample', type=int)
    args = parser.parse_args()
    sources = read_json(args.input)
    if not isinstance(sources, list):
        parser.error('Input must be a source array')
    if args.sample:
        sources = sources[:args.sample]
    valid, invalid, _ = await BatchValidator(timeout=args.timeout).validate_all(sources)
    files = {}
    for path, value in [(args.output, valid), (args.invalid, invalid),
                        (args.report, {'timestamp': utcnow(), 'total': len(sources), 'valid': len(valid), 'invalid': len(invalid)})]:
        if path:
            assert_report_output(path)
            files[path] = json_bytes(value)
    atomic_bundle(files)
    print(f'Valid: {len(valid)}; other results: {len(invalid)}')


if __name__ == '__main__':
    asyncio.run(main())
