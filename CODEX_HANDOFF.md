# Codex handoff: implement NHS Geography Spine v0.1–v0.2

You are implementing the repository described in `docs/SPEC.md`.

## Goal

Produce a reproducible public-data pipeline that maps NHS organisation sites and GP patient populations to July 2024 Westminster Parliamentary Constituencies.

## Do not redesign these core decisions

1. Keep **site-location** mappings separate from **served-population/catchment** mappings.
2. For NHS sites, prefer direct postcode -> PCON24 mapping.
3. Use the official ONS 2021 LSOA -> July 2024 Westminster constituency **best-fit** lookup for LSOA population aggregation.
4. Never silently drop unmapped postcodes, LSOAs or patient counts.
5. Every output must carry source/version provenance sufficient to reproduce it.

## First implementation sequence

### Task 1 — source discovery/adapters

Implement robust source adapters for:

- ONS LSOA21 -> PCON24 best-fit lookup (England/Wales)
- NHS England ODS GP practices predefined DSE report
- postcode geography source exposed by NHS ODS / ONS
- latest available NHS England quarterly GP practice-by-LSOA registered patient data

Prefer stable public CSV/download/API resources. If a landing page must be scraped to find the current file, isolate that logic in the adapter and fail loudly if expected resource patterns change.

Store raw downloads with source date and SHA-256 in the build manifest.

### Task 2 — canonical transformations

Implement:

- postcode normalisation
- ONS lookup normalisation and code validation
- ODS practice/site normalisation
- postcode -> PCON24 site mapping
- GP LSOA patient -> PCON24 aggregation

### Task 3 — outputs

Write:

- `data/processed/lsoa21_pcon24.parquet`
- `data/processed/nhs_organisation_sites.parquet`
- `data/processed/nhs_org_to_pcon.parquet`
- `data/processed/gp_practice_patient_pcon.parquet`
- `data/processed/nhs_geography.duckdb`
- `data/processed/build_manifest.json`
- `data/processed/qa_report.json`

Also export CSV versions of the two bridge tables.

### Task 4 — QA

Implement all hard-failure and warning rules in `docs/SPEC.md`.

Add tests with tiny local fixtures. Tests must run without network access.

### Task 5 — useful derived views

In DuckDB create:

- `pcon_nhs_organisations`
- `pcon_gp_patient_links`
- `gp_practice_pcon_profile`

Do not add MP names in the first pass unless you identify a reliable, versioned official/public source that can be refreshed independently. Constituency code/name is the stable join key; MP membership is a separate time-varying dimension.

## Engineering requirements

- Python 3.12+
- type hints on public functions
- no notebook-only logic
- idempotent builds
- deterministic column order and dtypes
- useful errors when upstream schemas change
- no large source data committed to Git
- document any source-specific licence/attribution requirements

## Acceptance commands

A clean environment should support, after dependency installation:

```bash
pytest -q
nhs-geo fetch
nhs-geo build
nhs-geo qa
```

`nhs-geo build` should be able to call fetch when sources are absent, unless `--offline` is supplied.

## Data-quality acceptance checks

- LSOA lookup key is unique.
- Patient totals reconcile exactly from source through PCON aggregation, with unmapped rows explicit.
- Valid active GP practice postcodes achieve >=99% PCON mapping or QA explains the miss.
- No use of town/name inference to manufacture geography.
- Every site mapping identifies `postcode_direct`, fallback, or `unmapped`.
- Every patient mapping identifies `lsoa21_best_fit`.

## Suggested next extension after v0.2

Once England GP mapping works reliably, add:

- branch surgeries
- NHS trusts/foundation trusts and sites
- ICBs and relevant commissioning locations
- Wales provider/commissioner sites
- Scotland and Northern Ireland site-location mapping via postcode -> Westminster constituency

Population/catchment mapping for Scotland and NI should use their own small-area geographies rather than pretending LSOA is UK-wide.
