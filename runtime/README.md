# Legado validation worker

The worker uses `lukelzlz/legado-server` at commit
`3cb7acb2a2892e71184c1dd12c8e8d7b5a60fbb7`.
The upstream engine and `NovelProbe.kt` use GPL-3.0; see [LICENSE](LICENSE).
Python communicates with a separate process through JSON lines.

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
.venv/bin/python -B scripts/probe_runtime.py gate --container-digest "$NOVEL_ENGINE_IMAGE"
.venv/bin/python -B scripts/daily_maintenance.py --mode catchup --no-collect --batch-size 300 --max-minutes 20
```

The sample command writes only `reports/runtime/`. It resumes the same input
and preserves per-revision results. It does not modify the raw inventory or
public files. Container and adapter identities must match across fixture and
sample reports. A native JVM fixture run cannot approve container publication.

`NOVEL_ENGINE_COMMAND` is an optional JSON argument array for local development.
It starts the compiled `io.legado.server.NovelProbeKt` class. Production uses
`NOVEL_ENGINE_IMAGE` with a SHA-256 identity. The container receives source JSON
over standard input and has no repository mount, Docker socket or GitHub token.

## Admission and limitations

Admission requires two distinct books and two chapters per book. Successful
search alone cannot refresh reading evidence or clear a reading failure.
The adapter records stage results, chapter hashes and sample counts.

Fixtures cover CSS, legacy selectors, JSONPath, JavaScript content, dynamic
search, a Java helper, dynamic discovery categories and adult-category rejection.
They do not prove compatibility with every Legado extension. Interactive
browser rules remain unsupported. Rules that cannot provide enough evidence
remain unverified; they are not confirmed site failures.

The publication gate requires passing container fixtures, 60 live sample
attempts with at least one complete reading success, at least 950 healthy sources
and 300 eligible reserves for the first
version 3 release. Later releases may report shortages but cannot use unverified
entries to fill them. Changing the adapter fingerprint invalidates old evidence.

## Reports

- `reports/runtime/fixtures.json`: deterministic JVM fixture results.
- `reports/runtime/sample.json`: bounded live sample results and resume identity.
- `reports/runtime/compatibility.json`: runtime approval requirements.
- `sources/legado/main/shadow.json`: candidate and reserve references, counts and gate status.
- `sources/legado/main/maintenance_report.json`: latest run and seven-day acceptance status.

Seven-day acceptance requires seven consecutive completed daily runs, at least
950 healthy sources, and at least 95% success in each 50-source spot check.
These observations cannot be replaced by unit-test results.
