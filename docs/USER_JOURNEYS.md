# Parliamentary Lens v0.5 user journeys

These tasks work against the frozen real-source candidate on **localhost only**.
The PR screenshots use fictional names/codes/figures from `web/synthetic.py`;
they demonstrate the interface, not NHS findings. A hosted real-data preview has
not been approved. All links below are relative to the candidate's `index.html`.

| Journey and task | Real candidate test IDs / expected result | Fictional browser test / screenshot | Interpretation and blind spots |
| --- | --- | --- | --- |
| **J1 — constituency/MP researcher.** Choose “Constituency and MP”, search Stockton North, open it, identify MP and distinguish its two main evidence sections. | `?view=constituency&code=E14001518`: Chris McDonald at 10 October 2026; 154 active coded addresses at the ODS snapshot; 327 GP practices with mapped registrations; 104,828 mapped registrations from 1 July 2026. Source clocks retain August 2026 postcode directory and July 2024 PCON. | `J1-researcher`, `X14000001` / [desktop screenshot](preview-evidence/J1-researcher-desktop.png) | The two counts answer different questions. Codes are not buildings; registrations are not census residents or care delivered. MP is current **at the dated snapshot**, not an assertion about today. |
| **J2 — cross-boundary GP.** Search Densham/A81001, identify its address constituency, follow its registration link to Stockton West and follow the GP link back. | `?view=organisation&code=A81001`: THE DENSHAM SURGERY, address Stockton North (`E14001518`); source list 3,733; Stockton North 1,917 (51.4%); Stockton West (`E14001519`) 1,725 (46.2%); unmapped 3. Constituency view explicitly identifies the address as being elsewhere. | `J2-cross-boundary`, `XG00001` ↔ `X14000002` / [desktop screenshot](preview-evidence/J2-cross-boundary-desktop.png) | Patient geography uses LSOA21 best fit, not exact addresses. Shares use the complete practice list, including unmapped registrations. A small registration count is not a patient identity or proof of care received. |
| **J3 — provider/commissioner researcher.** Open an operated site, inspect the actual RE6 record, follow its operator; then inspect an ICB. | `?view=organisation&code=A0J1B` (Northgate Health Centre) retains a current source RE6 to `RTH`; `RTH` is Oxford University Hospitals NHS Foundation Trust, address Oxford East (`E14001419`). `?view=organisation&code=QE1` is NHS Lancashire and South Cumbria ICB. Dates and availability appear beside the evidence. | `J3-provider-commissioner`, `XR00001` → `XT00001`, then `XI00001` / [ICB screenshot](preview-evidence/J3-commissioner-desktop.png) | Only the supplied RE6 is shown. The address, operator's address and unknown service catchment remain separate. “Foundation” in the name is not an inferred ODS subtype. No GP population is assigned to a trust/ICB. |
| **J4 — public phone user.** At 390px, choose organisation search, use a familiar practice/place name, read the dated result, copy its link and return to search. | Search `Densham` → `A81001`; show list extract date, address geography vintage and snapshot MP. Copy link reopens that code in the selected release. Familiar places work only where present in a record name; postcode/street search is explicitly unavailable. | `J4-phone-shareable`, `XG00001` / [phone screenshot](preview-evidence/J4-phone-390.png) | A useful answer is “this practice address maps here, with registrations allocated here, on these dates”. The loopback host's copied candidate URL retains the exact selected release across pin changes; opening the root selects the current pin. Provider permalink binding still needs approval/testing; readers must recheck dates. Target ≤2 minutes is for the human trial, not a measured completion claim. |

## Country coverage and absence

All 650 constituency/MP profiles cover the UK. Patient flows come from England
GP registrations. GP address reports include England/Wales; selected trust and
commissioner reports are mainly England, with some sites elsewhere. Scotland,
Wales and Northern Ireland do not have equivalent patient/provider coverage.
An empty panel means missing source evidence, never “no NHS services” or zero
residents. The interface states this on landing and on relevant empty panels.

## Edge cases that must remain interpretable

- Vacancy: fictional `X14000002`; show **Vacant** without inventing a member.
- Inactive organisation/effective RE6: `XB00001`; retain dates and identity, but
  explicitly withhold current parliamentary relationship evidence.
- Unresolved operator: `XU00001`; show source code without a missing profile link.
- GP without patient extract / unmapped address: `XE00001`; explain both absences.
- Empty and partial constituency: `X14000003` / `X14000004`; retain patient-only
  code `XP99999` as unlinked source context.
- Unknown/malformed link and missing snapshot asset: an announced, recoverable
  error with Back to search. Native Escape retains the search field; pending
  startup/search responses cannot reopen dismissed results.

Automated navigation and expected-output assertions are in
`web/tests/browser-check.mjs`; local real-source checks are recorded in the
[readiness report](PREVIEW_READINESS_REPORT.md). Human interpretation and
completion targets remain subject to the [trial protocol](USER_TEST_PROTOCOL.md).
