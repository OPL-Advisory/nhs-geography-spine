# Sources, attribution and refresh

## Frozen source set verified 9 October 2026

| Input | Official resource | Source date and meaning | Adapter |
| --- | --- | --- | --- |
| England/Wales LSOA21 → PCON24 best fit | [ONS lookup](https://geoportal.statistics.gov.uk/datasets/f0aac7ccbfd04cda9eb03e353c613faa/about) ([catalogue](https://ckan.publishing.service.gov.uk/dataset/lsoa-2021-to-westminster-parliamentary-constituency-july-2024-best-fit-lookup-in-ew1)) | July 2024 constituency geography; ArcGIS item created 16 September 2025 | Headered CSV; required fields, unique LSOA and code patterns validated. |
| GP practices | [NHS England ODS DSE epraccur report](https://digital.nhs.uk/services/organisation-data-service/data-search-and-export/csv-downloads/gp-and-gp-practice-related-data) and [report specification](https://www.odsdatasearchandexport.nhs.uk/referenceDataCatalogue/565791179.html) | Live report updated nightly; exact retrieval date/hash in manifest | Headerless 27-column report; RO76 records selected, including a pipe-separated RO76 role. Source status and postcode retained. |
| Postcodes and UK PCON24 names | [ONS NHS Postcode Directory full, August 2026](https://www.arcgis.com/home/item.html?id=55d0b9a92ed940f887dfd9a9402be0c8) | August 2026 directory; ArcGIS item created 19 August 2026 | Full zipped, headerless 49-column CSV per bundled NHSPD user guide, Annex A. The bundled December 2024 names-and-codes CSV is the code set. |
| GP patients by LSOA21 | [NHS England July 2026 release](https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice/july-2026), [CSV metadata](https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice/metadata) and [quality statement](https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice/data-quality-statement) | Extract snapshot 1 July 2026; latest quarterly LSOA release available on 9 October 2026 | Only `gp-reg-pat-prac-lsoa-all.csv` from the ZIP. `SEX=ALL` and extract period required; no male/female double counting. |

`data/raw/sources_manifest.json` records the exact URL, source version/date and date meaning, UTC retrieval time, byte count and SHA-256 of each downloaded file. `data/processed/build_manifest.json` copies that provenance and adds the Git SHA, build time, row counts, geography vintages and QA summary. The raw archives are never committed. The ODS source is a retrieval snapshot, not an immutable monthly publication.

## Geography and data-quality meaning

The postcode directory's `PCON` is a **direct postcode allocation** for the July 2024 constituency geography. A postcode can span addresses on both sides of a boundary; ONS assigns it from the postcode's reference location. The fallback from postcode LSOA to PCON is visibly marked and can differ from a direct allocation. Registered patients are allocated by the ONS **best-fit** LSOA lookup, which is not exact address-level or polygon-intersection geography.

England/Wales `lsoa_code` in `postcode_spine.parquet` means 2021 LSOA. The same source column has Scottish Data Zones and Northern Irish Super Data Zones for those countries. It must not be joined to the England/Wales best-fit lookup. The GP patient file also contains `EMPTY`, `CLOSED`, and small-area codes outside England/Wales. Those rows remain in an explicit `UNMAPPED` practice bucket and a reasoned detail table. The July CSV total differs from the release-page headline by six; no cause is assumed here.

The current ODS adapter selects RO76 GP practices, including England and Wales because the public epraccur report contains both. The patient data cover England GP registrations only. Branch surgeries, trusts, ICB sites and other providers/commissioners are outside this first adapter. An ODS practice without a patient-file row remains in the site bridge. A patient-file practice without an ODS site row remains in the patient bridge. Neither relationship is manufactured.

## Licences and attribution

The [ONS geography licences page](https://www.ons.gov.uk/methodology/geography/licences) states that the LSOA lookup is under OGL v3 and asks for: “Source: Office for National Statistics licensed under the Open Government Licence v.3.0”. For the postcode directory it also requires “Contains OS data © Crown copyright and database right 2026” and “Contains Royal Mail data © Royal Mail copyright and database right 2026”. **Northern Ireland postcode data require the separate LPS End User Licence for internal business use; commercial reuse requires a separate licence.** Review those terms before distributing the full UK postcode table or DuckDB bundle. This repository distributes code and source pointers, not the datasets.

[NHS England's terms](https://digital.nhs.uk/about-nhs-digital/terms-and-conditions) make its content available under the current OGL unless otherwise specified and require attribution for adapted content: “Contains information from NHS England, licenced under the current version of the Open Government Licence”. The GP patient publication is aggregate open data; source quality limitations remain those in its [data quality statement](https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice/data-quality-statement).

## Refresh procedure

1. Check the official ONS lookup and NHSPD item, the ODS epraccur download and report specification, and the NHS England GP registration series. Keep the geography vintage fixed at PCON July 2024 unless intentionally planning a migration. Confirm a patient ZIP explicitly contains **2021** LSOA and the ALL-persons member.
2. Update `config/sources.yml` with the verified resource URL, version, source date and ZIP member paths. The current file is pinned and runnable; the pipeline does not guess a new monthly/quarterly URL from a publication title.
3. Run `nhs-geo fetch --refresh`, `nhs-geo build --offline`, `nhs-geo qa`, and `pytest -q`. Inspect `build_manifest.json` hashes, `qa_report.json` mapping misses, source-versus-bridge patient totals, fallback counts and warnings. A schema or geography change fails the build rather than silently adapting.
4. Keep the exact raw archives plus manifest in controlled storage outside Git for any published release. Date the QA and attribution supplied with a downstream export. The generated Parquet, CSV and DuckDB files remain ignored by Git.

For the next expansion, add dedicated ODS branch-surgery, NHS trust/site and ICB/commissioner adapters with their source role and parent/relationship semantics. Then extend **site locations** to Wales, Scotland and Northern Ireland with the direct postcode mapping and licence review. Extend **served populations** only with defensible nation-specific small-area patient/provider flows; Scotland Data Zones and Northern Ireland SOAs are not England/Wales LSOAs.
