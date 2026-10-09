# Maintenance status

Updated: 2026-10-10

## Current direction

Maintain about 1,000 readable Legado sources and 500 eligible reserves through
an automated collection, validation, exclusion and replacement loop. Availability
takes priority over count. Explicit adult sites and adult sections are excluded;
ambiguous evidence is quarantined for contextual review.

Existing raw sources and newly collected sources feed the same inventory.
Collection runs alongside validation. Failed sources leave the eligible inventory,
qualified reserves fill the gaps, and scheduled runs keep checking upstream
revisions after the target count is reached. Raw records and failure evidence
remain available for later checks.

## Acceptance result

**The technical checks pass, but the 1,000-source objective has not passed
acceptance.** The latest two maintenance cycles produced one eligible source.
No public source files were published by these runs.

Runtime checked: `2ee5977b957031bb7556c1eaf7f716cc520e8d3e`.
The Python suite ran at `71955ed86f9ce6eec00d5a6adaa6a174fefd7af4`; the files in
`scripts/` and `tests/` are identical between those two commits.

| Check | Observed result |
| --- | --- |
| Python regression suite, local Python 3.14.6 and Actions Python 3.11 | 88 tests passed |
| JVM request, book-state, rule and network security regression tests | Passed during image build |
| Container reading fixtures | 10 passed, 0 failed |
| Separate live compatibility sample | 60 attempted, 1 complete reading success |
| Two maintenance cycles | 40 checks each, 1 complete reading success |
| Out-of-scope runtime attempts | 0 of 80, down from 19 of 80 in the baseline |
| Final shadow inventory | 1 healthy source, 0 eligible reserves; baseline was 0 and 0 |
| External feed collection | 5 files, 5,632 source definitions, 3,905 new revisions |
| YCKCEO collection | Reached the bounded collection time limit |
| Publication gate | Blocked by healthy and reserve counts |
| Seven-day operational acceptance | Not started |

The eligible source is Midureader. Its check completed two distinct books and
four distinct chapters. The separate compatibility sample does not insert its
successful source into the inventory. These two counts must not be combined.

Source definitions can repeat across feeds. New revisions are not a count of
unique sites or readable sources. The development acceptance job retains reports
only; its collected raw records are not imported into the production inventory.

- [Python pipeline run](https://github.com/tickmao/Novel/actions/runs/37948001065)
- [Container and maintenance run](https://github.com/tickmao/Novel/actions/runs/37953472900)
- [Previous baseline run](https://github.com/tickmao/Novel/actions/runs/37875758758)
- [Acceptance summary](reports/runtime/acceptance.json)
- [Compatibility evidence](reports/runtime/compatibility.json)
- [Live sample](reports/runtime/sample.json)
- [First cycle](reports/runtime/maintenance-cycle-1.json)
- [Second cycle](reports/runtime/maintenance-cycle-2.json)
- [Shadow inventory](reports/runtime/maintenance-shadow.json)

## Completed fixes

- Disabled and non-novel revisions are excluded before runtime scheduling. Their
  payloads and reasons remain in the raw store; an eligible new revision can
  enter the queue.
- Request parsing accepts spaced options and applies the declared search charset.
  JavaScript headers are evaluated, custom headers replace defaults, and the HTTP
  client retains control of host and connection framing. POST forms receive a
  content type when none is supplied.
- Book metadata is available to search URL templates and follows the book,
  catalog and chapter stages. JavaScript receives these fields without losing
  the book bridge methods. Source and address keys keep book contexts separate.
- Searches use prior successful book names and a source's `checkKeyWord` before
  generic titles. HTTP rejection is reported separately from rule errors.
- Maintenance reports retain each checked revision and its stage evidence.

The upstream compatibility patch is pinned and included in the runtime
fingerprint. Raw source rules are not rewritten to fit the validator.

## Remaining findings

The latest 80 maintenance attempts produced 65 unverified results, 8 transient
failures, 4 invalid results, 1 unsupported rule, 1 review result and 1 valid result.
Unverified and unsupported results do not prove a site fails in a user's app.

The QQ Browser source now extracts distinct books, resolves a catalog with
1,865 chapters, and reads a 1,913-character first chapter. A later sampled chapter
is empty or too short, so the source remains outside the eligible inventory.
Successful parsing alone cannot replace complete reading evidence.

Three representative HTTP 403 endpoints still rejected requests with both the
engine and browser User-Agent values. There is no evidence that changing the
default User-Agent alone resolves those failures.

## Remaining rollout work

1. Investigate the remaining source-specific rule and access failures using
   exact revisions and stage reports. Expand validation across both archived and
   newly collected sources without padding the inventory with unverified entries.
2. Accumulate qualified stock through persistent maintenance runs. Keep the
   first public release gated until at least 950 healthy sources and 300 eligible
   reserves are available; continue toward 1,000 and 500.
3. Verify the public snapshot and its mirrors, then observe seven consecutive
   daily runs with at least 950 healthy sources and at least 95% success in each
   50-source spot check.

GitHub CLI login is verified for `tickmao`. The production branch still uses the
previous maintenance workflow. At fetched commit `ce5c639`, its actual public
import file contains 194 entries while its internal file contains 1,000. These
counts are not readability evidence. The local 45-entry experimental snapshot
remains outside these commits. Historical inventories remain archived in
[the legacy audit](reports/legacy-audit/README.md).
