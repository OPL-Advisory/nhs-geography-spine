# Build v0.1 NHS–Parliamentary Geography Spine

## Objective

Implement the first working release of the NHS Geography Spine described in `CODEX_HANDOFF.md` and `docs/SPEC.md`.

The product must support two distinct questions:

1. **Location:** which Westminster constituency contains an NHS organisation/site?
2. **Population served:** which Westminster constituencies contain the registered patients served by a GP practice, and in what numbers/shares?

Do not collapse those into one relationship.

## Scope for this issue

- Discover and implement robust adapters for the official/public sources identified in the handoff.
- Build canonical postcode, LSOA21 -> PCON24 and NHS organisation/site transformations.
- Aggregate GP practice-by-LSOA registered patient counts to PCON24.
- Produce Parquet/CSV outputs, a DuckDB database, build manifest and QA report.
- Add offline tests using small fixtures.
- Document provenance, licences/attribution and operational refresh steps.

## Required outputs

- `data/processed/lsoa21_pcon24.parquet`
- `data/processed/nhs_organisation_sites.parquet`
- `data/processed/nhs_org_to_pcon.parquet`
- `data/processed/gp_practice_patient_pcon.parquet`
- CSV versions of the two principal bridge tables
- `data/processed/nhs_geography.duckdb`
- `data/processed/build_manifest.json`
- `data/processed/qa_report.json`

DuckDB views:

- `pcon_nhs_organisations`
- `pcon_gp_patient_links`
- `gp_practice_pcon_profile`

## Acceptance criteria

- `pytest -q` passes without network access.
- `nhs-geo fetch`, `nhs-geo build` and `nhs-geo qa` work in a clean Python 3.12+ environment after dependency installation.
- LSOA lookup keys are unique.
- GP patient counts reconcile exactly from source through constituency aggregation, including explicit unmapped buckets.
- Valid active GP practice postcodes achieve >=99% constituency mapping, or the QA report explains every miss.
- Every site mapping records its mapping method (`postcode_direct`, documented fallback, or `unmapped`).
- Every GP population mapping records `lsoa21_best_fit`.
- No town/name inference is used to manufacture geography.
- All downloaded inputs have source/version/retrieval/SHA-256 provenance.
- No large source or processed datasets are committed to Git.

## Implementation guidance

Treat `AGENTS.md`, `CODEX_HANDOFF.md` and `docs/SPEC.md` as the contract. Do not redesign their core decisions without documenting a concrete evidence-based reason in the PR.

Work on the pre-created `codex/v0.1-implementation` feature branch and open a PR back to `main`. In the PR description, include:

- sources actually used and their refresh dates;
- data quality summary and mapping rates;
- patient reconciliation results;
- known gaps;
- exact commands to reproduce the build;
- recommended next step for England provider/commissioner expansion and then Wales/Scotland/Northern Ireland.
