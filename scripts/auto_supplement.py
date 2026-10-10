"""Compatibility entry point for the unified catch-up runner."""
import argparse
import asyncio
from pathlib import Path
from daily_maintenance import DailyMaintenance


class AutoSupplement:
    def __init__(self, base_dir=None, export_target=None):
        self.maintenance = DailyMaintenance(base_dir)
        self.inventory = self.maintenance.inventory
        if export_target is not None and export_target != self.inventory.export_target:
            raise ValueError('Set inventory.export_target in the shared configuration')

    async def auto_supplement_workflow(self, force=False, dry_run=False):
        report = await self.maintenance.maintain(mode='catchup', batch_size=1000, dry_run=dry_run, publish=not dry_run)
        return report.get('published', False)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path)
    parser.add_argument('--target', type=int, default=1000)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    await AutoSupplement(args.base_dir, args.target).auto_supplement_workflow(args.force, args.dry_run)


if __name__ == '__main__':
    asyncio.run(main())
