# NHS Geography Spine

A reproducible public-data pipeline for two different questions:

- **Site location:** which July 2024 Westminster constituency contains a coded NHS provider or commissioner address?
- **Registered population:** which constituencies contain patients registered with a GP practice, allocated from 2021 LSOAs by the official ONS **best-fit** lookup?

The two bridges are separate. Postcode allocation uses the NHS Postcode Directory's direct constituency assignment; LSOA best-fit is a statistical allocation, not a polygon intersection or evidence that a practice site is in that constituency. Trust and commissioner entries locate coded addresses only; they do not measure service catchment or commissioning responsibility. No town or organisation-name inference is used. MP membership is intentionally a separate, time-varying dimension.

## Run from a clean checkout

Python 3.12 or newer is required. These commands install a regular wheel so the CLI works in a clean environment:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python '.[dev]'
source .venv/bin/activate
pytest -q
nhs-geo fetch
nhs-geo build --offline
nhs-geo qa
```

`nhs-geo build` also fetches missing sources by default; `--offline` verifies the cached hashes and never uses the network. `nhs-geo fetch --refresh` redownloads the pinned resources, including the nightly ODS report. Run the commands from the repository root, or pass `--config`, `--raw-dir`, and `--processed-dir` as needed. `nhs-geo export --format parquet|csv|duckdb --output-dir PATH` copies a built bundle.

The frozen source configuration is [config/sources.yml](config/sources.yml). Source choices, licences, refresh steps and limitations are in [docs/SOURCES.md](docs/SOURCES.md). Raw downloads and generated outputs are ignored by Git. Preserve a hashed copy of `data/raw` outside Git for a published release, since the ODS endpoint changes nightly.

## Outputs

| File in `data/processed` | Meaning |
| --- | --- |
| `lsoa21_pcon24.parquet` | Unique England/Wales LSOA21 to PCON24 best-fit lookup. |
| `postcode_spine.parquet` | Full August 2026 NHS Postcode Directory, including current and terminated UK postcodes and country-specific small-area codes. |
| `pcon24.parquet` | July 2024 UK constituency code/name set from the postcode archive. |
| `nhs_organisation_sites.parquet` | ODS RO76 GP practice records, with source roles, status and raw postcode retained. |
| `nhs_org_to_pcon.parquet` and `.csv` | One site-location mapping per ODS GP practice. `mapping_method` is `postcode_direct`, `postcode_to_lsoa_then_best_fit`, or `unmapped`. |
| `icb26_codes.parquet` | ONS April 2026 ICB code set used to select ICB records from the mixed ODS `eother` report. |
| `all_nhs_organisation_sites.parquet` | The GP records above plus ODS GP branches, trusts, trust sites, ICBs, Sub ICB units and their sites. One row per ODS code; role, status basis, source report, dates and explicit RE6 operator code are retained. |
| `all_nhs_org_to_pcon.parquet` and `.csv` | One postcode based location mapping per expanded ODS code, including explicit unmapped reasons. |
| `gp_practice_patient_pcon.parquet` and `.csv` | Practice by constituency registered-patient count and share of the practice list. `UNMAPPED` is an explicit bucket, not a PCON code. Every row records `lsoa21_best_fit` as the attempted method. |
| `gp_patient_unmapped_lsoa.parquet` | Original practice/LSOA rows that could not be allocated, with reason. |
| `gp_practice_source_totals.parquet` | Source total for each practice, retained for independent practice-level QA. |
| `pcon_nhs_organisations`, `pcon_provider_summary`, `organisation_pcon_profile` `.parquet` and `.csv` | Parliamentary location exports. The first includes an operating parent's name, type and address constituency when its code is present; the second counts mapped active provider and commissioner codes by type; the third retains mapped and unmapped organisation profiles. |
| `nhs_geography.duckdb` | The v0.1 dimensions, GP and patient bridges and views, plus expanded dimensions and the three parliamentary location views above. |
| `build_manifest.json`, `qa_report.json` | Input URLs, versions, retrieval times, SHA-256 hashes, row counts, conservation checks and mapping warnings. |

The `pcon_gp_patient_links` view's constituency share denominator is the mapped registered patients in that constituency **in this source file**. It is not a census population estimate. The practice profile's outside-address share uses the full source practice list, including the explicit unmapped bucket in the denominator.

`pcon_provider_summary` includes all active mapped types in `pcon_nhs_organisations`, including ICB and Sub ICB commissioner codes. It counts ODS codes, not distinct premises or hospitals. `organisation_pcon_profile` is an **address** profile, not a patient or service catchment. The existing GP-only files and patient views retain their v0.1 meaning. Source report semantics, coverage by type and known limits are in [docs/SOURCES.md](docs/SOURCES.md).

## Verified October 2026 build

The 9 October 2026 run used the July 2026 GP patient extract, the August 2026 NHS Postcode Directory, the July 2024 ONS constituency geography, and the ODS report retrieved that day. It yielded 35,672 unique LSOAs, 2,729,229 postcode records, 8,193 ODS GP practice records and 6,139 practices in the patient file. All 6,164 active England GP practice postcodes mapped directly to PCON24; the broader ODS report also includes 397 active Welsh practices, all directly mapped. There were no active site fallbacks or misses.

The source **ALL-persons CSV** contains 63,436,502 patients. The bridge conserves this exactly: 63,330,864 mapped and 105,638 (0.17%) explicitly unmapped. The unmapped count comprises 67,824 `EMPTY`, 36,066 `CLOSED`, and 1,748 outside England/Wales LSOA21 coverage. No valid England/Wales LSOA21 was missing from the ONS lookup. Five practices have more than 5% unmapped patients; 15 have less than 10% of their list in their address constituency. Those are review flags, not automatic errors. The NHS publication page headline is 63,436,508, six above the all-persons LSOA CSV; the pipeline reconciles to the file actually used and does not silently adjust it.

The report and manifest for a fresh run are generated locally; the numbers above describe the verified 9 October snapshot and will change after refresh.

## v0.2 real-source acceptance, 9 October 2026 UTC

The new ODS reports yielded 6,634 branch codes, 274 trusts, 46,388 trust-site codes, 321 Sub ICB unit codes and 2,189 Sub ICB location sites. The ONS April 2026 code set selected all 36 current ICBs from 1,089 mixed `eother` records. With the unchanged 8,193 GP practice records, the expanded dimension has 64,035 unique ODS codes. Of the active records, 45,937 have a direct PCON24 postcode mapping; there were no LSOA fallbacks. The parliamentary provider and commissioner summary contains 2,241 constituency-by-type rows. Four active trust-site codes have Isle of Man or Channel Islands postcodes with no PCON24 allocation and remain explicit unmapped rows. Every valid active England postcode in the included active types mapped.

There are 55,211 explicit RE6 operating links in the expanded reports; 55,142 resolve to a code in this dimension. The remaining 69 are GP branch links, 47 of them active, to prescribing cost centre codes outside the selected RO76 GP practice set. Their source parent codes are retained and reported; no parent is guessed. Another 15 active branches point to GP records marked `DORMANT` by `epraccur` and are flagged for review. The patient bridge still reconciles 63,436,502 people exactly, including 105,638 in the v0.1 unmapped bucket.
