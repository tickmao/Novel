# Legado validation worker

The worker uses `lukelzlz/legado-server` at commit
`3cb7acb2a2892e71184c1dd12c8e8d7b5a60fbb7`.
The upstream engine and `NovelProbe.kt` use GPL-3.0; see [LICENSE](LICENSE).
Python communicates with a separate process through JSON lines.

The image applies `legado-compatibility.patch` to that exact upstream commit.
The patch preserves source JSON while fixing spaced request options, declared
search encodings, JavaScript headers, header replacement, POST form content types,
and search URLs that use per-book metadata. The HTTP client retains control of
the target host and connection framing. The patch is part of the runtime
fingerprint; changing it invalidates previous reading evidence.

Book variables stay within the disposable worker and follow their book, catalog
and chapter addresses. They are isolated by source and address; response pages
are not cached.

Image builds run the focused compatibility tests and upstream rule and network
security regression tests before producing the worker.

Each source revision gets a new JVM, temporary database and resource budget.
The bridge calls `RuleRunner` directly. It does not use the reader's source
normalization, catalog cache, chapter cache or user credentials. Source JSON
stays unchanged. Responses include the engine commit and protocol identity.

## Container checks

Run from the repository root with Docker available:

```bash
docker build -f runtime/Dockerfile -t novel-runtime:local .
export NOVEL_ENGINE_IMAGE="$(docker image inspect novel-runtime:local --format '{{.Id}}')"
.venv/bin/python -B scripts/probe_runtime.py fixtures
.venv/bin/python -B scripts/probe_runtime.py sample --count 60 --max-minutes 20
.venv/bin/python -B scripts/probe_runtime.py gate --container-digest "$NOVEL_ENGINE_IMAGE" --require-ready
.venv/bin/python -B scripts/probe_runtime.py bench --count 60 --max-minutes 20 --concurrency-list 4,8,12
.venv/bin/python -B scripts/daily_maintenance.py --mode catchup --no-collect --batch-size 300 --max-minutes 20
```

The bench command runs one fixed sample at several concurrencies and writes only
`reports/runtime/benchmark.json`. Every probe starts its own JVM in a container
capped at one CPU, so the measurement decides `inventory.validation_concurrency`
(see `.github/workflows/probe-benchmark.yml`). It never writes inventory or
evidence, and the request budget stays harness configuration: changing it must
not raise `PROBE_PROTOCOL` or `VALIDATOR_VERSION`.

The sample command writes only `reports/runtime/`. It resumes the same input
and preserves per-revision results. It does not modify the raw inventory or
public files. Container and adapter identities must match across fixture and
sample reports. A native JVM fixture run cannot approve container publication.
Use `--require-ready` for acceptance: a blocked report then returns a nonzero
exit code. Inventory jobs can omit it to keep collecting without publication.

`NOVEL_ENGINE_COMMAND` is an optional JSON argument array for local development.
It starts the compiled `io.legado.server.NovelProbeKt` class. Production uses
`NOVEL_ENGINE_IMAGE` with a SHA-256 identity. The container receives source JSON
over standard input and has no repository mount, Docker socket or GitHub token.

## Admission and limitations

Admission requires one readable book with two distinct, non-empty chapters. That is
the way a reader uses a source: search, open one book, read its catalog. Two
chapters are required because a single chapter cannot prove the site is not serving
the same page for everything. A second readable book raises the badge to `stable`
and is evidence, not admission: its failure cannot write off a source that already
read. Successful search alone cannot refresh reading evidence or clear a reading
failure. The adapter records stage results, chapter hashes and sample counts.

Fixtures cover CSS, legacy selectors, JSONPath, JavaScript content, dynamic
search, a Java helper, dynamic discovery categories, adult-category rejection,
spaced request options and per-book URL metadata.
They do not prove compatibility with every Legado extension. Interactive
browser rules remain unsupported. Rules that cannot provide enough evidence
remain unverified; they are not confirmed site failures.

The publication gate requires passing container fixtures and 60 live sample
attempts with at least one complete reading success. Delivery is measured against
`inventory.usable_target` (500 sources that read), not a package size: the extended
package may hold more entries, but unverified entries never count as usable. A
source whose last successful reading check is older than `export_max_age_days`
drops out of the delivery on its own. Changing the adapter fingerprint invalidates old evidence,
and a previous release is carried as `legacy` until the new identity re-verifies it:
an admission change alone must never empty the published file.

## Reports

- `reports/runtime/fixtures.json`: deterministic JVM fixture results.
- `reports/runtime/sample.json`: bounded live sample results and resume identity.
- `reports/runtime/compatibility.json`: runtime approval requirements.
- `reports/runtime/benchmark.json`: concurrency comparison and its fixed decision rule.
- `sources/legado/main/shadow.json`: candidate and reserve references, counts and gate status.
- `sources/legado/main/maintenance_report.json`: latest run, probe funnel and seven-day acceptance status.

Seven-day acceptance requires seven consecutive completed daily runs that each
published, passed a 50-source spot check at 95%, and recorded at least 150 deep
attempts. A week in which probes ran but checked no new revision is reported as
pending: the loop was running, the delivery was not. These observations cannot be
replaced by unit-test results.
