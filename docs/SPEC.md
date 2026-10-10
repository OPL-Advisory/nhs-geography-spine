# NHS Geography Spine — implementation specification

## 1. Objective

Build a reusable UK-oriented geography layer that answers two different questions without conflating them:

1. **Where is an NHS organisation/site physically located?**
   - map the organisation/site postcode to the Westminster Parliamentary Constituency containing that postcode.

2. **Which constituencies does an organisation serve?**
   - for GP practices, aggregate registered-patient counts from small areas (initially LSOA in England) into constituencies.
   - for trusts/ICBs and other providers/commissioners, add catchment models later where a defensible population-flow dataset exists.

These mappings must remain separate in the data model.

## 2. Geography vintages

Initial canonical vintages:

- LSOA: 2021 England/Wales (`LSOA21CD`).
- Westminster constituencies: July 2024 boundaries (`PCON24CD`).
- Postcodes: latest ONS/NHS postcode directory snapshot used in each build.
- NHS organisations: ODS Data Search and Export snapshot/download date.

Do not silently mix 2011 and 2021 LSOAs or pre-2024 parliamentary constituencies.

## 3. Source datasets

### A. ONS LSOA -> constituency lookup (England and Wales)

Official ONS best-fit lookup:

- `LSOA21CD`
- `LSOA21NM`
- `LSOA21NMW`
- `PCON24CD`
- `PCON24NM`
- `PCON24NMW`
- `LAD21CD`
- `LAD21NM`

Purpose: population/small-area aggregation. Do not describe this as an exact polygon intersection: it is a best-fit allocation.

### B. Postcode geography

Use a current postcode directory capable of returning, at minimum:

- normalised postcode
- country
- OA / LSOA / MSOA where applicable
- LAD
- Westminster Parliamentary Constituency
- easting/northing and/or latitude/longitude where available
- termination status/date where applicable

Preferred first implementation: NHS ODS postcode files / ONS-supplied postcode data as exposed by NHS England, or ONS Postcode Directory if automation/licensing is straightforward.

### C. NHS organisation/site data

Use NHS England Organisation Data Service (ODS) Data Search and Export. DSE replaces the legacy fixed CSV publication process and predefined reports update nightly.

Initial organisation classes:

- GP practices
- GP branch surgeries
- NHS trusts / foundation trusts
- trust sites
- Integrated Care Boards and relevant sites/locations
- commissioning regions if useful
- Welsh Local Health Boards/sites

Retain ODS organisation role/type values rather than hard-coding only display labels.

### D. GP registered-patient small-area data

NHS England publishes Patients Registered at a GP Practice monthly and LSOA data quarterly. Use the latest quarterly LSOA-level practice registration file to build a *served population* mapping:

`practice_code + LSOA21CD -> patient_count -> PCON24CD`.

Then aggregate to:

`practice_code + PCON24CD -> patient_count, patient_share`.

This is analytically more useful for parliamentary engagement than practice-address constituency alone.

## 4. Core tables

### `dim_geography_lsoa21`

| column | type | notes |
|---|---|---|
| lsoa21cd | string | primary key |
| lsoa21nm | string | |
| lsoa21nmw | string nullable | Wales |
| lad21cd | string | |
| lad21nm | string | |
| pcon24cd_best_fit | string | ONS best-fit |
| pcon24nm_best_fit | string | |
| source | string | |
| source_version | string | |

### `dim_postcode`

| column | type | notes |
|---|---|---|
| postcode | string | canonical, uppercase with standard spacing |
| postcode_compact | string | no spaces, join helper |
| active | boolean | if source supports status |
| country_code | string | |
| oa_code | string nullable | |
| lsoa_code | string nullable | country-specific geography semantics must be documented |
| msoa_code | string nullable | |
| lad_code | string nullable | |
| pcon_code | string nullable | direct postcode allocation |
| pcon_name | string nullable | |
| latitude | double nullable | |
| longitude | double nullable | |
| source_snapshot_date | date | |

### `dim_nhs_organisation_site`

| column | type | notes |
|---|---|---|
| org_code | string | ODS code |
| site_code | string nullable | site identifier if distinct |
| org_name | string | |
| org_role | string | source role/type |
| parent_org_code | string nullable | |
| status | string | open/closed/current/etc |
| address_line_1 | string nullable | |
| town | string nullable | |
| postcode | string nullable | |
| open_date | date nullable | |
| close_date | date nullable | |
| source_snapshot_date | date | |

### `bridge_org_site_pcon`

One row per organisation/site location mapping.

| column | type |
|---|---|
| org_code | string |
| site_code | string nullable |
| postcode | string |
| pcon24cd | string nullable |
| pcon24nm | string nullable |
| mapping_method | string |
| mapping_quality | string |
| source_snapshot_date | date |

`mapping_method` allowed values initially:

- `postcode_direct`
- `postcode_to_lsoa_then_best_fit` (fallback only)
- `unmapped`

### `bridge_gp_practice_patient_pcon`

| column | type |
|---|---|
| practice_code | string |
| pcon24cd | string |
| pcon24nm | string |
| patient_count | integer |
| patient_share | double |
| lsoa_source_period | string |
| mapping_method | string |

`mapping_method = lsoa21_best_fit` in v1.

## 5. Transformation logic

### 5.1 Postcode normalisation

- uppercase
- trim leading/trailing whitespace
- remove all internal whitespace to create `postcode_compact`
- reconstruct display form using final 3 characters as inward code where valid
- never discard the original raw value until QA is complete

### 5.2 Organisation site mapping

1. Load current ODS rows.
2. Normalise postcode.
3. Left join to postcode spine on `postcode_compact`.
4. If postcode has a direct 2024 constituency code, use it.
5. If direct constituency is unavailable but a 2021 LSOA exists, optionally fall back to ONS LSOA best-fit and mark the method explicitly.
6. Never invent a constituency from town or organisation name.

### 5.3 GP patient mapping

1. Load quarterly practice-LSOA registered-patient counts.
2. Validate practice codes and `LSOA21CD` format.
3. Join `LSOA21CD` to ONS best-fit `PCON24CD`.
4. Sum patient counts by practice and constituency.
5. Calculate share of each practice list in each constituency.
6. Preserve unmapped patient counts and report them in QA; do not silently drop them.

## 6. QA rules

Hard failures:

- duplicate `LSOA21CD` in the official best-fit dimension
- invalid code patterns for canonical geographies
- patient count changes caused by aggregation (input total != mapped + explicitly unmapped total)
- duplicate postcode keys after canonicalisation unless source semantics explain them
- constituency codes outside the selected 2024 Westminster code set

Warnings:

- active NHS organisation with no postcode
- active organisation postcode absent from postcode spine
- organisation postcode maps through fallback rather than direct postcode constituency
- GP practice with >5% of registered patients unmapped
- GP practice address constituency contains <10% of registered patients (not necessarily wrong; useful flag for cross-boundary practices)

Recommended summary QA outputs:

- % active organisation sites mapped to constituency
- mapping rate by organisation type
- number of practices serving 1, 2, 3, 4+ constituencies
- median and 90th percentile share of GP lists outside the practice-address constituency
- top unmapped postcode values

## 7. UK extension

Do not force England/Wales LSOA semantics onto Scotland or Northern Ireland.

Create a common conceptual `small_area` interface with country-specific geographies beneath it:

- England/Wales: 2021 OA/LSOA/MSOA as appropriate
- Scotland: Data Zones / Intermediate Zones plus postcode lookup
- Northern Ireland: SOAs plus postcode lookup

For the *site-location* use case, postcode -> Westminster constituency is the common UK denominator and should be implemented first.

For population catchments, use each country's available small-area patient/provider datasets; mark coverage explicitly.

## 8. Repository layout

```
nhs-geography-spine/
  README.md
  pyproject.toml
  Makefile
  config/
    sources.example.yml
  src/nhs_geo_spine/
    __init__.py
    normalise.py
    ingest_ons.py
    ingest_ods.py
    ingest_gp_patients.py
    transform.py
    qa.py
    build.py
  tests/
    test_normalise.py
    test_transform.py
    test_qa.py
  docs/
    SPEC.md
  data/
    raw/          # gitignored
    processed/    # gitignored
```

## 9. CLI

Target commands:

```bash
nhs-geo fetch
nhs-geo build
nhs-geo qa
nhs-geo export --format parquet
nhs-geo export --format csv
nhs-geo export --format duckdb
```

The build must also be callable non-interactively in CI.

## 10. Suggested technology

- Python 3.12+
- `polars` for tabular transforms
- `duckdb` for bundle/query output
- `pyarrow` for Parquet
- `httpx` for downloads
- `pydantic` or dataclasses for source configuration
- `pytest` for tests
- optionally `geopandas` only when true polygon operations are needed; v1 should not require it

Avoid making spatial libraries a hard dependency for postcode/lookup joins.

## 11. Reproducibility and provenance

Every build writes `build_manifest.json` containing:

- build timestamp
- code version / git SHA
- source URLs
- retrieved timestamps
- source-provided dates/versions
- SHA-256 of each raw source
- row counts before/after each transform
- QA result summary

Do not rely on a source URL being permanently versioned; cache the exact raw file used for each published release outside Git if necessary.

## 12. Release strategy

### v0.1

- England/Wales LSOA21 -> PCON24 lookup
- England NHS GP practice/site -> PCON24 by postcode
- CSV + Parquet + DuckDB
- QA report

### v0.2

- Preserve the shipped GP registered-patient distribution and its v0.1 conservation checks.
- Add ODS GP branches, NHS trusts and sites, current ICBs, and Sub ICB units and sites under Issue #3.
- Publish parliamentary **address-location** views and explicit RE6 operating context; do not infer PCN/ICB or trust catchments from a site postcode.

### v0.3

- Implement Issue #5's parliamentary engagement layer on the fixed v0.1/v0.2 geography and organisation coverage.
- Add a separately refreshed UK Parliament current Commons member/vacancy dimension, full PCON24 validation, constituency briefs, ODS organisation profiles, three evidence-labelled relationship signals, CLI inspection and web-ready JSON.
- Preserve patient conservation and site-only non-GP semantics. Future provider/commissioner population flows need a separate defensible source.

### v1.0

- stable schemas
- automated source refresh
- release notes and provenance manifests
- documented country coverage matrix

## 13. Parliamentary-facing derived views

Add these only after the core spine is stable:

### `pcon_nhs_organisations`

For each constituency:

- MP constituency code/name
- GP practices physically located there
- GP branches physically located there
- provider/commissioner sites physically located there

### `pcon_gp_patient_links`

For each constituency:

- practices serving residents of the constituency
- patients attributed to each practice
- share of constituency registered population by practice where denominator is defensible

### `gp_practice_pcon_profile`

For each practice:

- address constituency
- constituencies served
- patient count and share per constituency
- number/share outside address constituency

These views support both 'which NHS organisations are in this MP's patch?' and 'which MPs matter to this provider because their constituents use it?'.

## 14. Codex acceptance criteria

Codex should not consider the first implementation complete until:

1. A clean checkout can rebuild all derived files from configured public sources.
2. Tests pass without network access using small fixtures.
3. The build produces a manifest with hashes and source dates.
4. LSOA input totals reconcile exactly through constituency aggregation, including an explicit unmapped bucket.
5. At least 99% of active England GP practices with a syntactically valid postcode map to a PCON24 code, or the QA report explains why the threshold is missed.
6. No best-fit LSOA mapping is presented as a geometric/exact intersection.
7. Site-location and patient-catchment mappings are separate tables and clearly labelled.
