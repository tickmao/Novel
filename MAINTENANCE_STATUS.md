# Maintenance status

Updated: 2026-10-10

## Operating policy

Validate the existing archive before collecting more source feeds. Previously
readable revisions receive priority, followed by other untested revisions.
Old success labels guide scheduling; only current complete reading and content
checks qualify a source for publication.

The archive contains 17,161 source identities. Scope and content checks leave
about 12,704 initial candidate revisions for runtime validation. Disabled,
non-novel and excluded content remain archived with their reasons.

Collection stays off while untested candidate revisions remain. Once the
eligible archive has been checked, collection can fill a qualified inventory
shortage. The target remains about 1,000 public sources and 500 reserves.

## Persistent validation result

Two independent GitHub Actions runs checked 500 and then 100 archived revisions.
The second run loaded the exact progress saved by the first, and the two batches
had no overlapping revisions. Both runs persisted their results to the branch.
No source feed was collected.

| Measure | Result after the second run |
| --- | --- |
| Archived source identities | 17,161 |
| Distinct revisions attempted | 600 |
| Completed deep attempts | 598; two runtime timeouts remain pending |
| Reading-success revisions | 25 |
| Selected healthy sources | 18 |
| Eligible reserves | 2 |
| Remaining candidate revisions | 12,106 |
| New source collection | 0 |
| Public source publication | Blocked by inventory thresholds |

Reading-success revisions can share a site or identical rules. Deduplication and
site limits reduce them to the selected inventory. Completed attempts include
unverified and failed results; they are not all usable sources.

The 1,000-source objective and seven-day availability acceptance are not complete.

- [First persistent scan: 500 revisions](https://github.com/tickmao/Novel/actions/runs/38015749897)
- [Independent continuation: 100 revisions](https://github.com/tickmao/Novel/actions/runs/38019287949)
- [Checkpoint acceptance summary](reports/maintenance/history-first-acceptance.json)
- [Latest maintenance report](sources/legado/main/maintenance_report.json)
- [Current shadow inventory](sources/legado/main/shadow.json)

## Automation and validation

The **Maintain Sources** workflow supports daily and catchup runs. Daily runs
check existing selected sources; catchup runs continue the raw backlog. Shadow
sources retain maintenance priority before the first public release. Raw records,
deep-check markers and shadow stock persist between runs.

Scheduled production maintenance runs at 02:00 Beijing time, with catchup runs
at 08:00, 14:00 and 20:00. Manual runs expose batch and time budgets. Development
branches can persist validation state but cannot publish public source files.

The first release requires at least 950 healthy sources and 300 eligible reserves.
It then continues toward 1,000 and 500. Publication updates all mirrors together.
Seven consecutive daily observations and the configured spot checks are still
required for operational acceptance.

Validation completed for this change:

- 94 Python regression tests passed locally and on GitHub Actions.
- The pinned JVM image passed its rule, book-state and network security tests.
- All 10 complete reading fixtures passed.
- Two persistent historical scans completed without new collection.
- The second run proved checkpoint continuity and no repeated batch.

## GitHub Actions failure review

The recent failure notifications came from development-branch runs:

- Runs [37948001063](https://github.com/tickmao/Novel/actions/runs/37948001063) and
  [37948395105](https://github.com/tickmao/Novel/actions/runs/37948395105) failed
  book metadata regression tests during image construction.
- Run [37949375963](https://github.com/tickmao/Novel/actions/runs/37949375963) failed
  the complete reading fixture because the JavaScript book bridge replaced
  supplied metadata. The compatibility fixes resolved these regressions.
- Run [37874597774](https://github.com/tickmao/Novel/actions/runs/37874597774) had
  zero complete live reading successes; its strict acceptance step correctly
  failed. Live-site variability remains distinct from code regression failures.

The subsequent [runtime check](https://github.com/tickmao/Novel/actions/runs/38014331116),
[Python check](https://github.com/tickmao/Novel/actions/runs/38014331081), both
persistent scans, and the latest checked legacy production maintenance run
[37999262598](https://github.com/tickmao/Novel/actions/runs/37999262598) succeeded.
See the [audit record](reports/maintenance/actions-audit.json).

## Tiered delivery and unattended loop

Yielding 0.85% reading successes per deep attempt cannot reach 1,000 published
sources by waiting longer: the archive would have to be re-checked roughly twenty
times. Delivery is therefore split by evidence instead of padded with unverified
entries.

- `full.json` holds only sources with current reading and content evidence. It
  stays below 1,000 while evidence is thin; it never falls back to an older
  unverified payload, and entries the current policy blocks are removed.
- `full-extended.json` repeats `full.json` byte for byte as its prefix, then
  fills toward `extended_target` with candidates ordered by
  `verified → grace → stale_verified → unverified → never_checked`. Disabled,
  non-novel, `invalid_url`, interactive-browser and access-gated revisions are
  excluded from both packages.
- `manifest.json` and `main/publication.json` record the per-source tier, the
  revision, both SHA-256 digests and the tier counts. `check_publication.py`
  rejects a release when the prefix invariant, the mirrors or the visible counts
  disagree.

The loop is deficit-driven rather than count-driven: `maintenance_plan.py`
chooses catchup or daily, the batch size and whether collection is allowed from
the current verified/extended gap and the remaining backlog. `network_selfcheck.py`
stops a run before it writes state when the runner has no outbound access, and the
incident guard now measures the current batch instead of requiring a healthy
control group. `ops_alert.py` keeps one `maintenance-degraded` issue in sync with
the delivery count and incidents.

Reading admission itself is unchanged: two books, two chapters each, at least 200
characters of distinct text, no access gate, no duplicate chapter bodies, plus the
content audit. Sampling v2 only widens how many candidates are tried before a
source is written off (ten keywords, up to four books, four chapter positions),
and the pipeline records which stage dropped each source.

Current measurements on the archive: the last recorded run made 100 deep attempts
and produced one complete reading success. The largest failure bucket is
`rule_error` (39 of 100), followed by `timeout` (14), `search_empty` (10),
`network` (9) and `http_forbidden` (8). Those are engine-coverage and sampling
questions, not proof that the sites are dead.

## Remaining work

Continue the archived-source scan and keep selected sources fresh. Investigate
unverified rules and isolated runtime failures using exact revisions and stage
reports. Collect new feeds only after the eligible historical backlog has been
checked and qualified stock remains short. Preserve the original payloads and
keep unsupported results separate from confirmed site failures.

Historical inventories remain available in [the legacy audit](reports/legacy-audit/README.md).
The original workspace's uncommitted experimental source files are not part of
these acceptance commits.
