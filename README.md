# NHS Geography Spine

A reproducible, versioned geography spine linking NHS organisations and population geographies to Westminster Parliamentary Constituencies.

## Initial scope

1. England and Wales: 2021 LSOA -> July 2024 Westminster Parliamentary Constituency.
2. England NHS organisations: ODS code + site postcode -> postcode geography -> constituency.
3. GP practices: both headquarters/site constituency and patient-list constituency distribution where published LSOA registrations are available.
4. Extend to Scotland and Northern Ireland using country-specific small-area geography/postcode sources while retaining UK Westminster constituency codes.

## Design principles

- Never overwrite source geography: retain source codes and vintages.
- Distinguish `site_location` from `population_catchment` mappings.
- Every derived mapping carries a `method`, `source`, `source_version`, `effective_date`, and QA status.
- Prefer exact postcode-point/ONSPD mapping for sites; use LSOA best-fit only for aggregate population mapping.
- Outputs are deterministic and rebuildable.

## Proposed outputs

- `lsoa21_pcon24.csv`: official England/Wales LSOA best-fit lookup normalised.
- `postcode_spine.parquet`: postcode -> OA/LSOA/MSOA/LAD/ICB/PCON and coordinates where licensed/available.
- `nhs_organisation_sites.parquet`: organisation/site records from ODS.
- `nhs_org_to_pcon.parquet`: organisation/site constituency mapping.
- `gp_practice_patient_pcon.parquet`: practice -> constituency patient counts/shares (England, where LSOA registration data permit).
- `nhs_geography.duckdb`: convenient analytical bundle.

See `docs/SPEC.md` for the full build specification.
