# Rights, attribution and privacy gate — 14 October 2026

**Real-data external preview and public release are not cleared.** This is an
evidence inventory and a maintainer decision gate, not legal approval. The code
and synthetic screenshots may be reviewed in the public GitHub PR; generated
real JSON/archives, postcode tables and raw extracts remain local and ignored.

## Source → output inventory

| Source and frozen vintage | Actual material in the candidate | Official terms/evidence and proposed notice | Status / narrow remaining question |
| --- | --- | --- | --- |
| ONS LSOA21 → July 2024 PCON best-fit lookup; item date 16 September 2025 | Aggregated practice/constituency registrations and best-fit descriptions, not LSOA rows/polygons. ONS ICB April 2026 code set also supplies ICB classification. | [ONS licences, lookup section](https://www.ons.gov.uk/methodology/geography/licences/) identifies non-postcode lookups as OGL. ONS source notice in the candidate's Sources and method section. [LSOA catalogue](https://geoportal.statistics.gov.uk/datasets/f0aac7ccbfd04cda9eb03e353c613faa/about). | Evidence supports OGL with attribution; maintainer must accept the actual derivative and notice. No exact address/polygon population claim. |
| NHSPD full August 2026; UK PCON names/codes bundled December 2024 reference | All 650 PCON code/name records; postcode-derived organisation/address and current parent-address allocations, counts and reverse lists. No postcode, coordinates or raw directory are projected into site JSON. | [Dataset catalogue](https://www.data.gov.uk/dataset/9ceffc36-e11c-4a86-be87-3ad50df431ba/nhs-postcode-directory-for-the-uk-full-august-2026) describes multiple rights holders; it has no dataset-specific licence value. [ONS postcode terms](https://www.ons.gov.uk/methodology/geography/licences/) distinguish GB OGL from NI. ONS/OS/Royal Mail 2026 notices are retained in `web/index.html`. | **Not cleared as a whole UK derivative.** Removing postcodes does not remove lineage. Confirm the bundled names/codes and each derivative's terms rather than treating government hosting as permission. |
| OS / Royal Mail components of GB postcode products | GB PCON address assignments and aggregates based on directory allocations. Not PAF address records or a purchased OS dataset. | [OS Code-Point Open guidance](https://www.ordnancesurvey.co.uk/documents/product-support/user-guide/code-point-open-overview-v3.1.pdf) identifies OGL and third-party acknowledgements for that product. Apply ONS NHSPD-specific notices, not an assumed PAF redistribution licence. Proposed 2026 notices are in the candidate HTML; directory version remains visible. | Confirm that the precise NHSPD derivative route is covered by the ONS terms, including Royal Mail rights. The OS product guidance is supporting evidence, not a licence for a different product. |
| LPS / OSNI Northern Ireland postcode component | **Five active site profiles have direct NI postcode → PCON assignments** in the real candidate (codes A3X4A, M4D5R, RFR0M, RRKE9, RTH61). They also feed constituency physical-location lists. The local postcode spine has 63,293 NI rows, including terminated records. | ONS says the supplied NI licence is **“for internal business use only”**. [LPS derived-data guidance](https://www.finance-ni.gov.uk/articles/lps-open-and-derived-data-questions-and-answers) covers data made using LPS inputs. [Current CPD terms](https://www.finance-ni.gov.uk/articles/digital-3-application-supply-and-licence-central-postcode-directory-cpd-terms-and-conditions) include internet permissions, third-party content and licence-number notices. | **Blocker.** Ask LPS/ONS: “May OPL Advisory distribute these five ODS-code → July 2024 PCON assignments, resulting per-PCON counts/lists and current parent-address links, without postcode/coordinate fields, in an authenticated research preview and later public site? Which licence, onward-download terms, notice and fee apply?” No licence number is invented. Do not simply delete NI rows without an explicit scoped-product decision and reconciliation of derivatives. |
| NHS England ODS DSE, seven reports retrieved 10 October 2026 | Names/codes, types, statuses, source dates, explicit RE6 identity/dates; postcode-derived address geography. Practitioner-person records are not used. Raw CSVs stay local. | [DSE description](https://digital.nhs.uk/services/organisation-data-service/data-search-and-export); [NHS England terms §8](https://digital.nhs.uk/about-nhs-digital/terms-and-conditions) generally permit copyright/database reuse under current OGL, with exceptions. Exact adapted-content notice is implemented in `web/index.html`. | Terms also say **“refresh cached content every 24 hours”**. Ask NHS England whether a clearly dated, immutable historical DSE derivative can be distributed without replacing historical release assets daily; agree a visible current-snapshot refresh policy. Do not assert a dated archive automatically satisfies this clause. |
| NHS England GP registrations, 1 July 2026 quarterly LSOA21 ALL-persons extract | Practice/constituency aggregate counts/shares and explicit unmapped buckets. No person-level patient data, diagnoses, opt-outs or care events. No raw practice/LSOA records exported. | [July release](https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice/july-2026), [metadata](https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice/metadata), [quality statement](https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice/data-quality-statement), and NHS terms above. Adapted-content NHS notice; extract period beside values. | Maintainer accepts statistical quality and small aggregate disclosure policy; clarify historical publication versus cache clause with NHS England alongside ODS. Source denominator is that practice's list, never a constituency population denominator. |
| Parliament Members API current constituency search, 10 October 2026 | Public professional MP name/party/status, PCON name bridge and date; no member photo, address, biography or logo. Vacancies explicit. | [API](https://members-api.parliament.uk/index.html); [Open Parliament Licence v3](https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/). Implemented: **“Contains Parliamentary information licensed under the Open Parliament Licence v3.0.”** with licence link. Licence excludes **“personal data in the information”** and disallows implied endorsement. | Maintainer documents the applicable permission/privacy basis for republishing published professional names; ask Parliament for API-specific confirmation if relying on OPL alone. An open API is not an exemption from the personal-data exclusion. Do not describe the snapshot as today's membership. |
| Application HTML/JS/CSS, system fonts; development tools | Four first-party static files. No third-party font download, analytics, tracking, map tiles, external scripts or imagery. Axe/Playwright are dev-only and not packaged in candidate. | System fonts are referenced, not redistributed. Playwright Apache-2.0 / axe-core MPL-2.0 notices remain in installed package metadata; package-lock pins versions. | GitHub reports **no repository code licence** and there is no root LICENSE. Maintainer must decide the code licence before promoting an open-source/public distribution claim. Data licences must not be subsumed under any eventual code licence. |

## Attribution and distribution scope

The proposed exact notices are inspectable in `web/index.html` under Sources and
method, including the adapted NHS wording and linked Parliament licence. They
are proposals awaiting acceptance; a notice is not clearance. Source vintages
are in each release, the header, major values and per-organisation footnote.
Any approved downloadable archive must retain notices, source URLs/versions,
source-manifest SHA, candidate hashes and its coverage statement. LPS-specific
notice must be agreed with a real licence number if required.

Never upload `data/raw`, `data/processed`, the DuckDB database, original JSON or
the derived site archive to an external service simply because the repository
is public. GitHub Releases are distribution too, even when described as a
preview; select only expressly approved artifacts/audiences.

## Privacy and accuracy review

The presentation contains organisation-level aggregate registrations and public
MP professional names. It contains no patient identifiers, person-level rows or
health conditions. Some practice/constituency cells are very small; aggregation
does not itself prove disclosure safety. Maintainer must record whether the
official source's disclosure controls remain sufficient after this recombination,
or approve and test a suppression policy that preserves totals and absence
semantics. No unreviewed suppression or deletion is introduced by this PR.

Dates differ: July patient list, August postcode directory, October ODS/MP, July
2024 constituency geography. MP/date mismatch, LSOA best fit, code/building
distinction, source gaps, inactive RE6 and unknown catchments are explicit.
Opt-out counts/rates/constituency estimates and dementia/frailty indicators stay
outside this product. Maintainer owns the final rights/accuracy/privacy decision.
No messages to rights holders have been sent; the questions above are ready for
the maintainer to use.
