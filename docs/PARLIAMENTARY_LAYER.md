# Parliamentary engagement layer (v0.3)

## Questions answered

`nhs-geo constituency <PCON24CD>` shows the current Commons member or vacancy, active coded NHS sites and organisations whose **addresses** map to the constituency, and GP practices with registered patients allocated there. `nhs-geo organisation <ODS_CODE>` shows an organisation/site address constituency and member, a source-backed RE6 operator where present, and, for a GP practice, all mapped patient constituencies and their current MPs. Add `--json` to either command for machine-readable output.

For example, the **offline test fixture** prints:

```text
$ nhs-geo constituency E14001063
Aldershot (E14001063)
MP: Member 123 (Example Party)
Site locations: 7 {'gp_branch_surgery': 2, 'gp_practice': 1, 'integrated_care_board': 1, 'nhs_trust_site': 1, 'sub_icb_location': 1, 'sub_icb_location_site': 1}
  site_location: B00001 Branch [gp_branch_surgery]
  site_location: B00002 Unresolved branch [gp_branch_surgery]
  site_location: A81001 Practice A81001 [gp_practice]
  site_location: QAA Example ICB [integrated_care_board]
  site_location: T00001 Trust site [nhs_trust_site]
  site_location: 00A Example sub ICB location [sub_icb_location]
  site_location: C00002 Mapped Sub ICB site [sub_icb_location_site]
GP practices serving residents: 1; mapped patients: 10
  registered_patients: A81001 Practice A81001 10 (62.5% of practice list)
  operating_relationship: C00001 -> 00A
  operating_relationship: C00002 -> 00A
  operating_relationship: B00001 -> A81001

$ nhs-geo organisation A81001
Practice A81001 (A81001) [gp_practice]
site_location address PCON: E14001063; MP: Member 123
Registered patients: 16; unmapped: 1
  registered_patients: E14001063 Aldershot 10 (62.5%); MP: Member 123
  registered_patients: E14001064 Aldridge-Brownhills 5 (31.2%); MP: Vacant
```

The member text and numbers are generated at build time. The readable commands show up to ten examples per section, ordered by type or patient count. `--json` returns every relationship. Both commands read the same versioned JSON files a web application can serve; they do not call the Parliament API live.

## Evidence model

| `relationship_basis` | Meaning | Patient fields |
| --- | --- | --- |
| `site_location` | An active ODS code's address postcode maps to PCON24. | Null. |
| `registered_patients` | A GP practice has patients in an LSOA21 best-fit allocation to PCON24. | Count, share of the full practice list and extract period. |
| `operating_relationship` | An active ODS site has an explicit RE6 operator code that is effective at its ODS source snapshot, and the **parent address** maps to PCON24. This does not claim the operator serves all residents there. | Null. |

One organisation/constituency can have multiple rows with different bases. They remain separate; no score or combined catchment is calculated. `site_in_constituency` compares the organisation/site's own address PCON with the relationship PCON, so a patient row may be true or false. An unresolved RE6 target remains in `organisation_parliamentary_profile` but cannot be assigned a parent-address constituency. Inactive ODS records retain a profile but do not produce current site or operator relationship rows. A patient-only GP code absent from the selected ODS dimension still appears in the `registered_patients` relationship table; its name and address are null rather than inferred.

An RE6 link is effective when its reported start date is on or before the organisation's ODS `source_snapshot_date` and its end date is on or after that date; missing bounds are open. Both boundaries are inclusive. Only active organisations with effective RE6 links produce current `operating_relationship` rows. Those rows carry the original RE6 start/end dates, source snapshot and `relationship_temporal_status=current`. The organisation profile and per-code JSON retain expired and future source RE6 records with `parent_relationship_temporal_status=expired` or `future`, their dates and raw parent address. For these records, `parent_relationship_basis`, `parent_current_address_pcon24cd` and parent MP fields are null, and the readable CLI explicitly says the source link is not current parliamentary evidence. `parent_address_pcon24cd` is retained as labelled source context, not a current parliamentary link. An inactive organisation may have an otherwise effective RE6 date range; `parent_relationship_current_at_snapshot=false` and its current parliamentary fields remain null.

`pcon_parliamentary_brief` has one row per PCON24, with member/vacancy, site counts by organisation type, serving practice count and mapped patient total. `mp_nhs_relationship` is the named/listable detail behind those counts. Per-PCON JSON separates `site_organisations`, `serving_gp_practices` and `operating_relationships`. `organisation_parliamentary_profile` has one row for every expanded ODS code and a JSON list of GP-only served constituencies, while the per-ODS JSON exposes the same list as an array and includes its individual relationships. The GP's total and `unmapped_patient_count` preserve the full source denominator; constituency totals include mapped patients only.

## Current member dimension and refresh

The official [UK Parliament Members API](https://members-api.parliament.uk/index.html) `Location/Constituency/Search` endpoint is paginated. Fetch combines every page into a canonical raw JSON file, then saves its URL, UTC retrieval date/time, version and SHA-256 in `data/raw/sources_manifest.json`. Its current representation can be null; such a constituency is retained with `member_status=vacant` and null member/party. The dimension also retains the Parliament constituency ID/name and the representation start date when supplied.

The API does not supply ONS `PCON24CD`. We join by Unicode-normalized, case-folded, whitespace-normalized name to the July 2024 names in the NHS Postcode Directory archive. The build requires a unique, complete match on both sides. This deliberately fails if a source changes constituency boundaries or names beyond that deterministic equivalence. It never silently drops a vacant seat or assigns an MP from a nearby name. A by-election only requires a new member snapshot:

```bash
nhs-geo fetch --refresh-member
nhs-geo build --offline
nhs-geo qa
```

The snapshot date and retrieval timestamp are visible in the member dimension, manifest and parliamentary QA. The MP shown is **current as of that snapshot**, not necessarily the MP at the older GP patient extract date or ODS source date. A later member refresh changes the association without rewriting historical patient counts. `nhs-geo qa` replays the parliamentary tables from the saved member, organisation and GP patient sources so that every copied member, source-period, share, organisation and site field must agree. It also parses every saved constituency and organisation JSON file and compares its full payload with the validated Parquet rows. Malformed, missing, extra or stale JSON fails QA offline. The build captures its Git SHA and dirty state before creating staging files. Whole-bundle publication uses an ownership marker and refuses custom destinations containing unrelated files or dangerous paths such as the repository root; the dedicated default directory can migrate an earlier known output bundle.

## Limits

The July 2024 PCON boundary and August 2026 postcode assignments are fixed in this release. An address postcode allocation is a source-assigned point location and may not precisely identify a building split by a boundary. LSOA21 best-fit is a statistical allocation of GP registrations, not a person-level location or exact intersection. Patient counts are registrations, not service use. Site presence and an RE6 operator address do not establish a trust/ICB catchment, a commissioning responsibility or a provider's service area. The ODS reports and their role coverage have the limits in [SOURCES.md](SOURCES.md). This layer has no inferred non-GP patient distribution or parliamentary relevance score.
