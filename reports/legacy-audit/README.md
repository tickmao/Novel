# Legacy source audit and recovery proposal

Audit date: 2026-09-30. This audit made no network requests and changed no source inventory, publication, or remote workflow.

## Findings

The replacement pipeline removed most legacy delivery entries because of its own validation limits. The archived payloads were not lost.

The actual import file contained **306** sources before the current uncommitted rewrite. The internal main file contained **1,000**. Both files have identical content in commits `dfb374935e299b8c04476a91e3d978acf424b1e0` and its parent `f573e2494eb3b5c22ce6b7ca34620770d9990982`. Their union contains 1,233 exact source URLs; they must not be treated as one consistent historical release.

| Exclusive review category | Public baseline | Internal baseline |
| --- | ---: | ---: |
| URL retained in the current snapshot | 3 | 5 |
| Content flag that requires evidence review | 25 | 53 |
| Validator does not support the rules | 218 | 586 |
| Temporary failure | 14 | 42 |
| Probe failure, site failure not confirmed | 41 | 74 |
| No completed verification | 5 | 235 |
| Stored valid status, URL not retained | 0 | 5 |
| Total | 306 | 1,000 |

Content flags take priority over the remaining validation categories. Thus, the public baseline has 221 unsupported validation statuses, but 3 also have content flags and appear in the content review queue. Of those 221 statuses, **199** report JavaScript or dynamic expressions. Unsupported does not mean broken in Legado.

All **306 public payloads and 1,000 internal payloads** were found by exact content digest in the raw archive. The audit records each baseline revision separately from later revisions and from other revisions with the same rule fingerprint. A later revision's evidence is not silently assigned to the original payload.

The current snapshot has 45 entries. **41 have matching-rule provenance in the legacy archive; 4 have YCKCEO provenance.** Only 6 exact URLs occur in the union of the two old delivery lists. Most current entries therefore come from old raw inventory outside those delivery lists, not newly downloaded sources. Eight current rule fingerprints match the baseline union; this comparison ignores URL fragments, unlike the exact-URL count.

## What is and is not established

- Archive preservation is verified by exact payload digests, not only by source counts.
- Neither old validation labels nor historical inclusion prove that a source works today.
- An unsupported rule is a validator coverage gap. It is not evidence for removal.
- Search failure, HTTP errors, TLS errors, and empty results from this implementation do not establish that Legado cannot read the source. The 41 public probe failures remain unconfirmed.
- The audit establishes no new confirmed site failures. It uses existing observations without an independent Legado check.
- The public content queue contains 11 entries with static flags and 14 flagged only by recorded page text or a later revision. These are not 25 confirmed adult sites.
- Some static evidence is explicit: the archived ESJ source contains an `R18` discovery section and `/tags-01/R18/{{page}}.html` links. That source definition should stay out of a clean recovery set under the agreed content boundary.
- Several other flags retain only a matched word such as `色情`, without its surrounding text. This cannot distinguish a prohibited-content disclaimer from an adult category. These records need contextual review; they should not receive an automatic clean or blocked verdict.

## Recovery proposal

1. **Freeze the local replacement workflow.** Do not run collection-led publication or push the current workflow changes while recovery is under review. The current 45-entry file remains a local experimental snapshot. Remote workflows were not changed by this audit.
2. **Use the 306-entry public file as the delivery baseline.** Preserve original rules and identities. Treat the 1,000-entry internal file as a separate inventory to reconcile, not as a known-good release to restore blindly.
3. **Review content evidence first.** Keep explicit adult categories and already justified exclusions quarantined. Review the 25 public content flags using the exact source revision, full field context, and, where necessary, a fresh page capture. Do not infer site content from a genre name or isolated word.
4. **Establish Legado validation coverage.** Start with representative old sources across JavaScript/dynamic requests, CSS selector variants, and static request parsing. Compare search, book, catalog, and chapter results in a compatible Legado execution environment. Do not start another bulk static crawl to answer this question.
5. **Separate legacy retention from new-source admission.** A legacy source may remain explicitly unverified while its unsupported rules are investigated. New additions still require content review and reading evidence. A source cannot be called healthy merely because it was previously listed. The 72-hour grace rule is for a previously verified transient failure, not for an unsupported validator.
6. **Prepare a reviewable restoration candidate.** List each retained, quarantined, and removed baseline entry with its reason and exact revision. Do not automatically export the 281 public entries outside the flag queue as clean; absence of a flag is not proof of clean content. Keep the recovery candidate separate from public import files until this review is complete.
7. **Resume replenishment after the baseline is understood.** Fill demonstrated gaps from the existing raw inventory first. Target 1,000 delivered sources and 500 reserve sources as an ongoing supply objective, not as a reason to replace the delivery baseline with whatever the static validator happens to support.

Selecting and integrating a compatible Legado execution environment is the next technical decision. This audit does not claim that such an environment is installed or that full JavaScript rules have been validated.

## Artifacts and reproduction

- `baseline-public.json`: byte-for-byte committed import file.
- `baseline-internal.json`: byte-for-byte committed internal main file.
- `audit.json`: 1,306 per-baseline rows, exact archive references, validation evidence, content flags, related rule revisions, and current-source provenance.

```bash
.venv/bin/python -B scripts/audit_legacy_sources.py
```

The baseline commit is pinned by default. Regeneration reads the current archive and snapshot, so current evidence may change after future runs. Baseline hashes and the current snapshot hash are recorded in `audit.json`.
