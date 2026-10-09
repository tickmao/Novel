# Maintenance status

Updated: 2026-10-09

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

GitHub CLI login is verified for `tickmao`. The development branch has completed
container and bounded live checks. **The 1,000-source objective has not passed
acceptance.** No public source files were published by these runs.

Code checked: `d6a913b9e9f98f01699baa19fbe01df777f41673`.

| Check | Observed result |
| --- | --- |
| Python regression suite on GitHub Actions, Python 3.11 | 83 tests passed |
| Local regression suite, Python 3.14.6 | 83 tests passed |
| Container rule fixtures | 8 passed, 0 failed |
| Separate live compatibility sample | 60 attempted, 1 complete reading success |
| First maintenance cycle | 40 checked, 0 eligible sources |
| Second maintenance cycle | 40 checked, 0 eligible sources |
| External feed collection | 5 files, 5,632 source definitions, 3,905 new revisions |
| YCKCEO collection | Reached the bounded collection time limit |
| Final shadow inventory | 0 healthy sources, 0 eligible reserves |
| Publication gate | Blocked by healthy and reserve counts |
| Seven-day operational acceptance | Not started |

Source definitions can repeat across feeds. New revisions are not a count of
unique sites or readable sources. The compatibility sample is separate from
inventory admission; its one success does not imply an eligible public inventory.

The workflow's successful result means its bounded technical checks completed.
It does not establish the product's inventory or sustained availability targets.
An earlier run of the same validation code had zero complete successes out of
60 attempts and failed the strict acceptance step. The previously successful
source returned different books, and one sampled chapter was empty or too short.
This variability remains unresolved.

- [Python pipeline run](https://github.com/tickmao/Novel/actions/runs/37875758757)
- [Final container and maintenance run](https://github.com/tickmao/Novel/actions/runs/37875758758)
- [Earlier failed live acceptance](https://github.com/tickmao/Novel/actions/runs/37874597774)
- [Acceptance summary](reports/runtime/acceptance.json)
- [Compatibility evidence](reports/runtime/compatibility.json)
- [Live sample](reports/runtime/sample.json)
- [First cycle](reports/runtime/maintenance-cycle-1.json)
- [Second cycle](reports/runtime/maintenance-cycle-2.json)
- [Shadow inventory](reports/runtime/maintenance-shadow.json)

## What the live checks exposed

The two maintenance cycles recorded 51 unverified results, 21 invalid results,
5 transient failures, 2 unsupported rules and 1 review result. No source passed
complete reading and content admission in those 80 attempts.

Nineteen attempts were rejected as disabled or non-novel sources. Thirty-five
reported rule errors, including upstream HTTP 403 responses; 16 could not extract
enough distinct books. Unverified and unsupported results do not prove that the
site fails in a user's Legado app.

External collection continued despite individual feed errors. YCKCEO reached its
time limit while other feeds added revisions. Both maintenance cycles completed
without an engine incident, and the second cycle used the updated raw store.
The development acceptance job retains reports only; its collected raw records
are not imported into the production inventory.

## Remaining rollout work

1. Exclude known disabled and non-novel inputs before runtime scheduling, while
   retaining their raw records and reasons.
2. Investigate rule and search failures against representative exact revisions.
   Compare blocked upstream requests with an appropriate execution environment;
   do not convert unsupported or unconfirmed failures into verified passes.
3. Accumulate qualified stock through persistent maintenance runs. Keep the
   first public release gated until at least 950 healthy sources and 300 eligible
   reserves are available; continue toward 1,000 and 500.
4. Verify the public snapshot and its mirrors, then observe seven consecutive
   daily runs with at least 950 healthy sources and at least 95% success in each
   50-source spot check.

The current production branch still uses the previous maintenance workflow.
At fetched commit `ce5c639`, its actual public import file contains 194 entries
while its internal file contains 1,000. These counts are not readability evidence.
The local 45-entry experimental snapshot remains outside the acceptance commits.
Historical inventories remain archived in [the legacy audit](reports/legacy-audit/README.md).
