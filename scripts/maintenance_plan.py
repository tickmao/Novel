"""Decide the next maintenance run from inventory counts instead of a fixed schedule."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from source_store import read_json

DEFAULTS = {
    'extended_target': 1000,
    'verified_target': 950,
    'reserve_target': 500,
    'reserve_min': 300,
    'catchup_batch': 1500,
    'daily_batch': 300,
}


def plan(*, verified_count=0, extended_count=0, reserve_count=0, pending_revisions=0,
         config=None, forced_mode=None):
    """Return one deterministic run decision.

    ``collect`` is only allowed once the raw backlog is exhausted, because every
    pending revision already costs less than a new network download.
    """
    cfg = {**DEFAULTS, **(config or {})}
    extended_target = int(cfg['extended_target'])
    verified_target = int(cfg['verified_target'])
    reserve_target = int(cfg['reserve_target'])
    reserve_min = int(cfg['reserve_min'])
    extended_gap = max(0, extended_target - int(extended_count))
    reserve_gap = max(0, reserve_target - int(reserve_count))
    verified_gap = max(0, verified_target - int(verified_count))
    shortage = extended_gap > 0 or reserve_gap > 0 or verified_gap > 0
    mode = forced_mode or ('catchup' if shortage else 'daily')
    batch_size = int(cfg['catchup_batch'] if mode == 'catchup' else cfg['daily_batch'])
    collect = bool(shortage and int(pending_revisions) == 0)
    if not shortage:
        reason = 'inventory_sufficient'
    elif int(pending_revisions):
        reason = 'raw_inventory_pending'
    else:
        reason = 'qualified_inventory_shortage'
    return {
        'mode': mode, 'batch_size': batch_size, 'collect': collect, 'reason': reason,
        'extended_gap': extended_gap, 'verified_gap': verified_gap, 'reserve_gap': reserve_gap,
        'pending_revisions': int(pending_revisions),
        'publish_extended': True,
        'publish_full': int(verified_count) >= verified_target and int(reserve_count) >= reserve_min,
    }


def explain(decision, counts):
    lines = [
        '## Maintenance plan', '',
        f"- mode: `{decision['mode']}` (batch {decision['batch_size']})",
        f"- collect: `{decision['collect']}` ({decision['reason']})",
        f"- verified: {counts.get('verified_count', 0)} / target",
        f"- extended: {counts.get('extended_count', 0)} (gap {decision['extended_gap']})",
        f"- reserves: {counts.get('reserve_count', 0)} (gap {decision['reserve_gap']})",
        f"- pending revisions: {decision['pending_revisions']}",
        f"- publish full: `{decision['publish_full']}`, extended: `{decision['publish_extended']}`",
    ]
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--explain', action='store_true', help='Print a Markdown summary for the run log')
    parser.add_argument('--mode', choices=['daily', 'catchup'])
    args = parser.parse_args()
    from source_inventory import SourceInventory

    inventory = SourceInventory(args.base_dir)
    config = read_json(args.base_dir / 'config/supplement_config.json', {}).get('inventory', {})
    progress = inventory.revalidation_progress()
    _, _, counts = inventory.select()
    decision = plan(verified_count=counts.get('verified_count', 0),
                    extended_count=counts.get('extended_count', 0),
                    reserve_count=counts.get('reserve_count', 0),
                    pending_revisions=progress.get('pending_revisions', 0),
                    config=config, forced_mode=args.mode)
    print(explain(decision, counts) if args.explain else json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
