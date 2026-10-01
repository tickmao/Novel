# Maintenance status

Updated: 2026-10-01

## Current direction

The approved objective is a sustainable public inventory of about 1,000 readable
Legado sources, with 500 eligible reserves. Availability takes priority over
count. Explicit adult sites and adult sections are excluded; ambiguous evidence
is quarantined for contextual review.

The historical 306-source public file and 1,000-source internal file remain
archived in [the legacy audit](reports/legacy-audit/README.md). They are inputs to
revalidation, not proof of current readability. The former bulk static-only
publication sequence has been replaced by runtime and inventory gates.

## Implemented locally

- Added a disposable JVM worker for exact source revisions, using
  `lukelzlz/legado-server` at `3cb7acb2a2892e71184c1dd12c8e8d7b5a60fbb7`.
  The worker calls the rule core without reader caches or source normalization.
- Added two-book, four-chapter admission, dynamic discovery review, stage
  evidence, runtime fingerprints and explicit unsupported/environment outcomes.
- Kept full reading evidence separate from search results. Search success cannot
  clear a reading failure or extend a transient grace period.
- Added contextual content evidence and revision-specific review decisions.
  Site exclusions also cover alternative source fragments for the same site.
- Added a first-release gate: passing container compatibility evidence,
  60 live sample attempts, at least 950 healthy sources and 300 eligible reserves.
  The old 45-source experiment cannot bypass this gate.
- Added shadow snapshots, shared writer locks, bounded collection and validation,
  incremental feed downloads, GitHub subdirectory discovery and submission ingestion.
- Updated CI for Python 3.11, a pinned runtime image, recovery artifacts,
  daily/catchup maintenance, 50-source spot checks and seven-day acceptance.

## Verified locally

- 70 Python tests passed on Python 3.11.15 and Python 3.14.6.
- The pinned JVM engine and bridge compiled with JDK 21.
- 35 upstream rule compatibility and network security tests passed.
- Eight actual JVM fixture cases passed: CSS, legacy selectors, JSONPath,
  JavaScript content, a Java helper, dynamic search, dynamic discovery and
  adult-discovery exclusion.
- Publication checksum and mirror consistency checks passed.
- Workflow YAML parsing and `git diff --check` passed.

Fixture results are in [reports/runtime/fixtures.json](reports/runtime/fixtures.json).
These are native JVM offline checks, not container or live-site acceptance.

## Remaining rollout requirements

The local machine has no Docker executable. GitHub CLI is not authenticated.
No container run, 60-source live sample or remote workflow has completed yet.
The seven-day observation period has not started. Development-branch CI is
configured to run a bounded sample without publishing public source files.

The compatibility report correctly remains `blocked`: it has no container
digest or live sample evidence. Existing raw source payloads and public snapshots
have not been rewritten by this implementation.

1. Authenticate GitHub CLI and use the reviewed CI changes, or provide a Docker
   environment. See [runtime setup](runtime/README.md).
2. Run container fixtures and the 60-source sample; retain unsupported and
   unconfirmed failures as such.
3. Accumulate qualified inventory in shadow mode. The initial public release
   stays gated until the 950/300 minimum is met; continue toward 1,000/500.
4. Verify the first publication, then collect seven consecutive daily observations
   with at least 950 healthy sources and at least 95% success in each spot check.

The original raw archive has 17,161 source identities. Its previous static results
do not meet the new runtime evidence requirement. A zero eligible count during
migration means revalidation is pending, not that all historical sites failed.
