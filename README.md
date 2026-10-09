# NHS Geography Spine

A reproducible public-data pipeline for two different questions:

- **Site location:** which July 2024 Westminster constituency contains an NHS GP practice postcode?
- **Registered population:** which constituencies contain patients registered with a GP practice, allocated from 2021 LSOAs by the official ONS **best-fit** lookup?

The two bridges are separate. Postcode allocation uses the NHS Postcode Directory's direct constituency assignment; LSOA best-fit is a statistical allocation, not a polygon intersection or evidence that a practice site is in that constituency. No town or organisation-name inference is used. MP membership is intentionally a separate, time-varying dimension.

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
| `gp_practice_patient_pcon.parquet` and `.csv` | Practice by constituency registered-patient count and share of the practice list. `UNMAPPED` is an explicit bucket, not a PCON code. Every row records `lsoa21_best_fit` as the attempted method. |
| `gp_patient_unmapped_lsoa.parquet` | Original practice/LSOA rows that could not be allocated, with reason. |
| `gp_practice_source_totals.parquet` | Source total for each practice, retained for independent practice-level QA. |
| `nhs_geography.duckdb` | The dimensions and bridges, plus `pcon_nhs_organisations`, `pcon_gp_patient_links`, and `gp_practice_pcon_profile` views. |
| `build_manifest.json`, `qa_report.json` | Input URLs, versions, retrieval times, SHA-256 hashes, row counts, conservation checks and mapping warnings. |

The `pcon_gp_patient_links` view's constituency share denominator is the mapped registered patients in that constituency **in this source file**. It is not a census population estimate. The practice profile's outside-address share uses the full source practice list, including the explicit unmapped bucket in the denominator.

## Verified October 2026 build

The 9 October 2026 run used the July 2026 GP patient extract, the August 2026 NHS Postcode Directory, the July 2024 ONS constituency geography, and the ODS report retrieved that day. It yielded 35,672 unique LSOAs, 2,729,229 postcode records, 8,193 ODS GP practice records and 6,139 practices in the patient file. All 6,164 active England GP practice postcodes mapped directly to PCON24; the broader ODS report also includes 397 active Welsh practices, all directly mapped. There were no active site fallbacks or misses.

The source **ALL-persons CSV** contains 63,436,502 patients. The bridge conserves this exactly: 63,330,864 mapped and 105,638 (0.17%) explicitly unmapped. The unmapped count comprises 67,824 `EMPTY`, 36,066 `CLOSED`, and 1,748 outside England/Wales LSOA21 coverage. No valid England/Wales LSOA21 was missing from the ONS lookup. Five practices have more than 5% unmapped patients; 15 have less than 10% of their list in their address constituency. Those are review flags, not automatic errors. The NHS publication page headline is 63,436,508, six above the all-persons LSOA CSV; the pipeline reconciles to the file actually used and does not silently adjust it.

The report and manifest for a fresh run are generated locally; the numbers above describe the verified 9 October snapshot and will change after refresh.
