"""Export review evidence or record a revision-specific content decision."""

import argparse
import json
from pathlib import Path
from source_inventory import SourceInventory
from source_store import atomic_bundle, json_bytes, read_json, utcnow


def review_queue(inventory):
    rows = []
    for record in inventory.store.records():
        revision = record['latest_revision']
        version = record['versions'][revision]
        audit = version.get('audit', {})
        if audit.get('decision') not in ('review', 'block'):
            continue
        rows.append({'source_id': record['source_id'], 'revision': revision,
                     'name': version['source'].get('bookSourceName'), 'audit': audit})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'decide'])
    parser.add_argument('--base-dir', type=Path)
    parser.add_argument('--source-id')
    parser.add_argument('--revision')
    parser.add_argument('--evidence-hash')
    parser.add_argument('--decision', choices=['pass', 'block'])
    parser.add_argument('--reason')
    parser.add_argument('--reviewer')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    inventory = SourceInventory(args.base_dir)
    rows = review_queue(inventory)
    if args.action == 'decide':
        if not all((args.source_id, args.revision, args.evidence_hash, args.decision, args.reason, args.reviewer)):
            parser.error('A decision requires source ID, revision, evidence hash, reason and reviewer')
        row = next((r for r in rows if r['source_id'] == args.source_id and r['revision'] == args.revision), None)
        if not row or row['audit'].get('evidence_hash') != args.evidence_hash:
            parser.error('Review evidence is missing or has changed; refresh the queue')
        audit = row['audit']
        if audit['decision'] == 'block' and args.decision == 'pass':
            parser.error('Explicit exclusions require a policy correction, not a review override')
        decision = {key: audit[key] for key in ('policy_version', 'payload_sha256', 'evidence_hash')}
        decision.update(source_id=args.source_id, revision=args.revision, decision=args.decision,
                        reason=args.reason, reviewer=args.reviewer, checked_at=utcnow())
        path = inventory.project_root / 'config/content_reviews.json'
        data = read_json(path, {'schema_version': 1, 'decisions': []})
        data['decisions'].append(decision)
        atomic_bundle({path: json_bytes(data)})
        print(json.dumps(decision, ensure_ascii=False, indent=2))
    elif args.output:
        atomic_bundle({args.output: json_bytes(rows)})
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
