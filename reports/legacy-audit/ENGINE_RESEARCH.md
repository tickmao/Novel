# Legado runtime feasibility

Update (2026-10-01): the approved maintenance implementation now evaluates the
separate `lukelzlz/legado-server` fork at a pinned commit. Its native JVM build,
rule tests and offline pipeline fixtures have been checked. Container and live
source validation remain pending. See [runtime setup](../../runtime/README.md)
and [maintenance status](../../MAINTENANCE_STATUS.md). The findings below describe
the repositories checked on 2026-09-30, not every available fork.

Checked on 2026-09-30 against public upstream repositories. This is source research; no engine was installed or run.

## Finding

Do not build the recovery plan around `hectorqin/reader:latest` or a current official Legado download. Neither checked upstream currently provides the historical validation engine. Keep Python `unsupported` results as unknown. These findings do not show that the affected book sources fail in a user's existing app.

| Candidate | Verified upstream state | Consequence |
| --- | --- | --- |
| Official `gedoor/legado` | The current root commit is `9bb0569269ed557753fc3866250970de6d454fcf`, dated 2026-05-27. Its README announces removal of project content. The current tree has no application engine. The releases API returns an empty array. | No current official Android runtime, JVM library, API implementation, or release artifact was available to verify. |
| Historical JVM `hectorqin/reader` | `master` is the root commit `07b320efb1b41b813d923d3ba38a1a41bc427dd5`, dated 2026-06-22. Its only file is README with `deleted`. Tags and releases return empty arrays. | No historical JVM JAR or endpoint implementation can be verified through the current branch or release list. |
| Current `hectorqin/reader` | `main` at `3b42dcaec6d180d1376a6435574cf99cfc386c95` is a TypeScript self-hosted book/media library with Docker deployment. Its README documents local files, OPDS, and OpenList, not a Legado rule interpreter. | The repository name alone does not establish compatibility. Do not substitute its search/library endpoints for historical Legado source validation. |

Primary sources:

- [Official Legado pinned README](https://github.com/gedoor/legado/blob/9bb0569269ed557753fc3866250970de6d454fcf/README.md), [commit metadata](https://api.github.com/repos/gedoor/legado/commits/9bb0569269ed557753fc3866250970de6d454fcf), [releases](https://api.github.com/repos/gedoor/legado/releases).
- [Reader historical branch pinned README](https://github.com/hectorqin/reader/blob/07b320efb1b41b813d923d3ba38a1a41bc427dd5/README.md), [commit metadata](https://api.github.com/repos/hectorqin/reader/commits/07b320efb1b41b813d923d3ba38a1a41bc427dd5), [tags](https://api.github.com/repos/hectorqin/reader/tags), [releases](https://api.github.com/repos/hectorqin/reader/releases).
- [Reader current pinned README](https://github.com/hectorqin/reader/blob/3b42dcaec6d180d1376a6435574cf99cfc386c95/README.md), [current source tree](https://api.github.com/repos/hectorqin/reader/git/trees/3b42dcaec6d180d1376a6435574cf99cfc386c95?recursive=1).

## Versions, APIs, and licensing

No exact legacy endpoint is recommended: its implementation could not be inspected in these current primary sources. An old tutorial's endpoint name is not sufficient evidence for request fields, response semantics, authentication, or JavaScript compatibility.

The current Reader README identifies AGPL-3.0 and links its [pinned license](https://github.com/hectorqin/reader/blob/3b42dcaec6d180d1376a6435574cf99cfc386c95/LICENSE). This does not establish the license or dependency requirements of any separately recovered historical JVM build. The checked current Legado tree has no license file; repository metadata reports no detected license. Obtain the actual pinned historical source and its license before adopting a recovered runtime.

There is no verified downloadable local runtime from the checked upstream releases. Third-party APKs, JARs, container registries, forks, and previously cached artifacts were not audited. The absent releases and root-only legacy histories do not prove that all historical artifacts are unavailable elsewhere.

## Smallest viable next step

1. Complete the recovery manifest and a small representative validation queue without executing JS. Include plain HTTP/HTML, inline JS, `java.*` helpers, login/cookies, and WebView-dependent rules. Keep stable source IDs and original rules unchanged.
2. Locate one provenance-verifiable existing app or historical engine artifact. Record its version, source commit where available, checksum, license, and runtime requirements. Prefer a known user app for reference behavior; a JVM port must be assessed separately.
3. Inspect that exact implementation before adding an adapter. Run a local fixture through search, book details, TOC, and content, then a small selected real-source sample. Store stage results and runtime identity. A successful search alone is not a full pass.
4. Only after this compatibility gate, choose JVM automation or Android-device/emulator automation. Keep execution isolated from production data and credentials because source rules can execute code and issue network requests.

The parent task's local preflight found Node but no Docker, `adb`, or Java runtime. Node availability does not resolve Java/Android helper semantics. A JVM route would need a compatible JRE plus a verified engine; an Android route would need an existing device or emulator plus a verified app. Neither dependency investment is justified until the artifact is selected.

Unresolved decision: which exact historical runtime or maintained successor is acceptable as the reference? Recovery bookkeeping, content review, and protection of the old inventory can proceed now; unsupported sources must not be relabeled as verified passes or confirmed failures while this decision remains open.
