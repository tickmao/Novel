"""Standalone reading validation with complete resumable checkpoints."""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from pathlib import Path

from runtime_validator import RuntimeReadingValidator
from runtime_config import runtime_fingerprint
from source_health import apply_result, VALIDATOR_VERSION
from source_policy import SourcePolicy
from source_store import atomic_bundle, digest, json_bytes, read_json


class BatchValidator:
    def __init__(self, batch_size=200, concurrency=20, timeout=10, checkpoint_dir=None):
        self.batch_size, self.concurrency, self.timeout = batch_size, concurrency, timeout
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self.input_hash = None

    async def validate_batch(self, sources, batch_num=1, total_batches=1):
        valid, invalid = [], []
        validator = RuntimeReadingValidator(SourcePolicy(), concurrency=min(self.concurrency, 4))
        for offset in range(0, len(sources), self.concurrency):
            batch = sources[offset:offset + self.concurrency]
            results = await asyncio.gather(*(validator.probe(source) for source in batch))
            for source, result in zip(batch, results):
                item = deepcopy(source)
                version = {'validation': deepcopy(item.get('_health', {})), 'audit': deepcopy(item.get('_audit', {}))}
                apply_result(version, result)
                item.update(_health=version['validation'], _audit=version['audit'],
                            _validation_status=result['status'], _last_validated_at=result['checked_at'],
                            _validation_level='reading' if result['status'] == 'valid' else None,
                            _validation_error=result.get('error'))
                (valid if result['status'] == 'valid' else invalid).append(item)
        stats = {'total': len(sources), 'valid': len(valid), 'invalid': len(invalid),
                 'batch_num': batch_num, 'valid_rate': len(valid) / len(sources) if sources else 0}
        return valid, invalid, stats

    def save_checkpoint(self, batch_num, valid, invalid, stats):
        if self.checkpoint_dir:
            payload = {'schema_version': 2, 'input_hash': self.input_hash, 'validator_version': VALIDATOR_VERSION,
                       'runtime_fingerprint': runtime_fingerprint(),
                       'batch_num': batch_num, 'batch_size': self.batch_size, 'valid': valid, 'invalid': invalid, 'stats': stats}
            atomic_bundle({self.checkpoint_dir / 'validation.json': json_bytes(payload)})

    def load_checkpoint(self):
        return read_json(self.checkpoint_dir / 'validation.json') if self.checkpoint_dir else None

    async def validate_all(self, sources, resume=False):
        self.input_hash = digest(sources)
        valid, invalid, stats, start = [], [], [], 0
        if resume and (checkpoint := self.load_checkpoint()):
            if (checkpoint.get('input_hash') != self.input_hash or checkpoint.get('batch_size') != self.batch_size
                    or checkpoint.get('validator_version') != VALIDATOR_VERSION):
                raise ValueError('Checkpoint does not match the input or validator')
            if checkpoint.get('runtime_fingerprint') != runtime_fingerprint():
                raise ValueError('Checkpoint uses another runtime adapter')
            valid, invalid, stats = checkpoint['valid'], checkpoint['invalid'], checkpoint['stats']
            start = checkpoint['batch_num'] * self.batch_size
        for offset in range(start, len(sources), self.batch_size):
            batch_num = offset // self.batch_size + 1
            yes, no, report = await self.validate_batch(sources[offset:offset + self.batch_size], batch_num)
            valid.extend(yes)
            invalid.extend(no)
            stats.append(report)
            self.save_checkpoint(batch_num, valid, invalid, stats)
        return valid, invalid, stats


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-valid', type=Path, required=True)
    parser.add_argument('--output-invalid', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=200)
    parser.add_argument('--concurrency', type=int, default=20)
    parser.add_argument('--timeout', type=int, default=10)
    parser.add_argument('--checkpoint-dir', type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    from validate import assert_report_output
    assert_report_output(args.output_valid)
    assert_report_output(args.output_invalid)
    validator = BatchValidator(args.batch_size, args.concurrency, args.timeout, args.checkpoint_dir)
    valid, invalid, _ = await validator.validate_all(read_json(args.input), args.resume)
    atomic_bundle({args.output_valid: json_bytes(valid), args.output_invalid: json_bytes(invalid)})


if __name__ == '__main__':
    asyncio.run(main())
