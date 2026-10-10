# NHS Parliamentary Lens — static candidate

This is a read-only browser consumer of the accepted v0.3 JSON and QA contracts. It makes no browser calls to NHS, ODS, ONS or Parliament APIs and has no backend, cookies, analytics or secrets. The site builder reads the accepted `data/processed` bundle, reruns saved-output QA, projects only fields needed for the pages, creates sorted search indexes and per-code details, and publishes a complete local directory atomically. The output is ignored by Git.

## Build and preview

From the repository root, after an offline `nhs-geo build --offline` and `nhs-geo qa`:

```bash
python web/build.py --processed-dir data/processed --output-dir web/dist \
  --archive /tmp/nhs-parliamentary-lens-candidate.tar.gz
python -m http.server 8765 --bind 127.0.0.1 --directory web/dist
```

Open `http://127.0.0.1:8765/`. Search by constituency name/PCON24 code or organisation name/ODS code; the typeahead supports arrow keys, Enter, Escape, Tab and a visible focus treatment. Direct routes can be copied, for example `?view=constituency&code=E14001063` and `?view=organisation&code=A81001`. The home page loads the shell and `release.json`; it fetches the two indexes only when someone types, and fetches one detail on navigation. Site and patient relationships are never merged into one generic “served by” statistic.

`release.json` pins a content-addressed snapshot, input manifest SHA-256, source code SHA, QA state, row counts, dates and coverage warnings. `asset-hashes.json` records every accepted v0.3 source JSON hash, generated detail/index hash and static shell hash. The compressed tarball is a deterministic candidate and rollback artifact. Never commit the generated tree or raw postcode data to Git.

## Measured candidate and budgets

The local 10 October 2026 accepted-source candidate contains 650 constituency pages and 64,040 organisation pages. It passed saved-output QA with documented mapping warnings. Measurements from this candidate:

| Item | Observed | Proposed gate for next release |
|---|---:|---:|
| Initial HTML/CSS/JS plus release record, gzip sum | 11 KB | ≤ 25 KB |
| Two lazy search indexes, gzip sum | 659 KB | ≤ 800 KB |
| Largest single constituency detail | 604 KB uncompressed | ≤ 1 MB |
| Largest single organisation detail | 98 KB uncompressed | ≤ 250 KB |
| Whole site | 225 MB uncompressed / 22.8 MB `.tar.gz` | ≤ 30 MB archive |
| Static detail objects | 64,690 | Hosting file-count and upload-time gate **not yet exercised** |

These are candidate budgets, not a hosting or accessibility certification. The many small detail files are a deployment concern; test host file count, upload time, caching and 404 handling in a separate preview before selecting a platform. If this exceeds host limits, revise packaging while retaining lazy record fetches and exact per-code routes. The single search request is ~659 KB compressed and may be noticeable on slow mobile links; measure it in a network-throttled preview before production approval.

## Source dates, coverage and rights

Each page distinguishes the Parliament member snapshot, GP registered-patient extract, ODS source snapshot and PCON24/postcode geography vintage. A current MP is **not** a historical MP at the earlier GP extract. An ODS address PCON is **not** a care catchment; a GP list allocation is **not** an exact resident count. Missing source evidence has a reason, not a zero. See [Parliamentary Layer](../docs/PARLIAMENTARY_LAYER.md) and [Sources](../docs/SOURCES.md).

Attribution appears in the site footer/method panel. NHS England content is generally under the current OGL unless a source says otherwise; ONS LSOA lookup is OGL v3. The NHS Postcode Directory includes OS and Royal Mail rights, and Northern Ireland postcode data have separate LPS internal-use terms with commercial reuse requiring a separate licence. This candidate excludes the raw postcode directory and detailed postcodes, but derived allocations still need a rights review, particularly any Northern Ireland lineage, **before public hosting**. Review the actual release notices and downstream attribution; do not assume this repository's code licence covers every data asset.

## Candidate release and rollback

1. Build and QA the spine against pinned raw sources; retain the raw hashes/manifest outside Git. Run `python -m pytest -q`, `ruff check src tests web spikes` and `node --test web/tests/*.test.mjs`.
2. Build the site with an archive as above. Record its SHA-256, `snapshot_id`, input manifest SHA-256, browser checks and the measured budget. Inspect sample current MP, vacancy fixture, GP cross-boundary and RE6 historical states. Confirm rights, privacy, accessibility and host file-count decisions with the maintainer.
3. For a separately approved preview or release, upload the **immutable archive or its extracted directory** under the recorded snapshot ID, verify file hashes and route/search behavior, then switch the hosting pointer only after smoke checks. Do not overwrite a known-good snapshot in place. Production deploy and DNS changes are outside this PR.
4. Roll back by restoring the prior pinned artifact/hosting pointer and verifying its prior `release.json` ID and sample deep links. The local builder keeps the previous marked `web/dist` if projection or final rename fails; tests inject both failures. It refuses unrelated nested files and nested source/output paths rather than deleting them.

The current site is a review candidate only. No opt-out, dementia or frailty data are included. See [opt-out assessment](../docs/OPT_OUT_INTEGRATION_SPIKE.md) and [dementia/frailty roadmap](../docs/DEMENTIA_FRAILTY_INTEGRATION_ROADMAP.md) for separate gates.
