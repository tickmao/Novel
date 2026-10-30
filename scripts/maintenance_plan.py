"""Decide the next maintenance run from inventory counts instead of a fixed schedule."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from source_store import read_json

DEFAULTS = {
    'extended_target': 1000,
    'verified_share_target': 0.3,
    'reserve_target': 500,
    'reserve_min': 300,
    'catchup_batch': 1500,
    'daily_batch': 300,
}


def plan(*, verified_count=0, extended_count=0, reserve_count=0, pending_revisions=0,
         config=None, forced_mode=None):
    """Return one deterministic run decision.

    ``collect`` follows the shortage alone. It used to wait for the raw backlog to
    reach zero, which shut harvesting down for as long as the backlog took to drain
    and restarted that wait after every evidence reset. The run budget already caps
    collection at a share of the schedule, so harvesting runs alongside validation.
    """
    cfg = {**DEFAULTS, **(config or {})}
    extended_target = int(cfg['extended_target'])
    verified_share_target = float(cfg['verified_share_target'])
    # One truth: the absolute floor follows from the share and the package size.
    verified_target = round(extended_target * verified_share_target)
    reserve_target = int(cfg['reserve_target'])
    reserve_min = int(cfg['reserve_min'])
    extended_gap = max(0, extended_target - int(extended_count))
    reserve_gap = max(0, reserve_target - int(reserve_count))
    verified_gap = max(0, verified_target - int(verified_count))
    shortage = extended_gap > 0 or reserve_gap > 0 or verified_gap > 0
    mode = forced_mode or ('catchup' if shortage else 'daily')
    batch_size = int(cfg['catchup_batch'] if mode == 'catchup' else cfg['daily_batch'])
    collect = bool(shortage)
    if not shortage:
        reason = 'inventory_sufficient'
    elif int(pending_revisions):
        reason = 'raw_inventory_pending'
    else:
        reason = 'qualified_inventory_shortage'
    return {
        'mode': mode, 'batch_size': batch_size, 'collect': collect, 'reason': reason,
        'extended_gap': extended_gap, 'verified_gap': verified_gap, 'reserve_gap': reserve_gap,
        'verified_target': verified_target, 'verified_share_target': verified_share_target,
        'pending_revisions': int(pending_revisions),
        'publish_extended': True,
        'publish_full': int(verified_count) >= verified_target and int(reserve_count) >= reserve_min,
    }


def explain(decision, counts):
    lines = [
        '## Maintenance plan', '',
        f"- mode: `{decision['mode']}` (batch {decision['batch_size']})",
        f"- collect: `{decision['collect']}` ({decision['reason']})",
        f"- verified: {counts.get('verified_count', 0)} / {decision['verified_target']}"
        f" ({counts.get('verified_share', 0):.0%} of {decision['verified_share_target']:.0%})",
        f"- extended: {counts.get('extended_count', 0)} (gap {decision['extended_gap']})"
        f", searchable {counts.get('searchable_count', 0)}"
        f" ({counts.get('searchable_share', 0):.0%} = verified ceiling)",
        f"- reserves: {counts.get('reserve_count', 0)} (gap {decision['reserve_gap']})",
        f"- pending revisions: {decision['pending_revisions']}",
        f"- publish full: `{decision['publish_full']}`, extended: `{decision['publish_extended']}`",
    ]
    return '\n'.join(lines)
def collection_budget(max_minutes, *, collect=True):
    """Seconds reserved for harvesting before validation starts.

    Validation fills whatever window it is given, so without a reservation the
    collection decision was 'allowed' and then overridden by
    'run_budget_exhausted' on every run: harvesting never actually started.
    """
    if not collect:
        return 0.0
    return min(600.0, float(max_minutes) * 60 * 0.15)




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
