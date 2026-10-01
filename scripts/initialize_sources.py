"""Initialize raw storage and run the unified validation pipeline."""
import argparse
import asyncio
from pathlib import Path
from daily_maintenance import DailyMaintenance
from source_policy import SourcePolicy


class InitializeSources:
    def __init__(self, base_dir=None):
        self.maintenance = DailyMaintenance(base_dir)
        self.inventory = self.maintenance.inventory

    def quick_filter(self, sources):
        return self.inventory.policy.screen_sources(sources)[0]

    async def initialize(self, **options):
        if options.get('skip_validation'):
            raise ValueError('Production validation cannot be bypassed')
        self.inventory.ensure_migrated()
        return await self.maintenance.maintain(mode='catchup', batch_size=options.get('batch_size', 1000),
                                               concurrency=options.get('concurrency', 20),
                                               timeout=options.get('timeout', 10), publish=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path)
    parser.add_argument('--batch-size', type=int, default=1000)
    parser.add_argument('--concurrency', type=int, default=20)
    parser.add_argument('--timeout', type=int, default=10)
    parser.add_argument('--target', type=int, default=1000)
    args = parser.parse_args()
    initializer = InitializeSources(args.base_dir)
    if args.target != initializer.inventory.export_target:
        parser.error('Use the shared inventory target configuration')
    await initializer.initialize(batch_size=args.batch_size, concurrency=args.concurrency, timeout=args.timeout)


if __name__ == '__main__':
    asyncio.run(main())
