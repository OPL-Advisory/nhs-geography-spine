# Parliamentary Lens v0.5 — preview readiness

**Decision: GO for local/offline review; NO-GO for an external real-data
controlled preview until the gates below are signed. Public launch is NO-GO.**
No preview URL exists, no provider was mutated, and no public deployment, DNS,
analytics, indicator publication or automatic merge is part of this work.

## Candidate and usefulness

v0.5.0 consumer, based on merged v0.4 main `c17ab5a`. Clean source-build commit
`78cd4bc739ea7bdc7cf810b866d544cf336402d9`; site implementation `2bd1b59`
adds only synthetic-fixture/storage-test changes after that build. The accepted
pipeline retains its v0.3 data contract. Later PR commits add evidence/docs only.

| Real local artifact | SHA-256 |
| --- | --- |
| Source/build manifest | `b69ab89148b1f73a49ae7359d078aac26b1a6c159313e39eb85a704ff0efe95d` |
| Snapshot / asset-hash manifest | `30609c6033cf33c11e1fafe881d23ffbca1ee1237a8037ac971de9b2893a14bc` |
| Release JSON | `d4af8fcf0b06b06a5142b750982f340a8168dd07e0d619bced6e46ff7721de07` |
| Candidate archive | `0325d760f940cb53e5b136afc6e0f9a4b1e206163af5ef6217288534f41f6d40` |

The [ledger](preview-evidence/real-candidate-ledger.json) contains raw input
hashes, clocks and all four static-file hashes. Real generated data/archives
stay local. Reproducing the same archive requires the same accepted processed
bytes/manifest; a new pipeline run records a new build time and manifest.

All [four journeys](USER_JOURNEYS.md) pass on the **real local candidate**:
dated constituency/MP, two-way cross-boundary GP navigation, provider/ICB source
relationships, and phone search/copy/back. [Real navigation evidence](preview-evidence/real-journey-results.json)
and [fictional screenshots](preview-evidence/J4-phone-390.png) are distinct.
Entry-point selection, dates beside values, country/absence caveats and explicit
cross-boundary links address concrete interpretation friction. Familiar-place
search still depends on record names; postcode/street search is unavailable.

## Validation, capacity and rollback

- **119 Python tests, 12 Node tests, Ruff and diff checks pass.**
  [Implementation Actions](https://github.com/OPL-Advisory/nhs-geography-spine/actions/runs/38646985978)
  passed; the review PR's checks must also pass on its final head.
- Fresh frozen-source pipeline and saved-output QA: `pass_with_warnings`;
  **63,436,502 registrations, reconciliation difference 0**. Retained warnings:
  4 active unmapped provider addresses, 47 unresolved active RE6 links,
  105,638 explicitly unmapped registrations. All 64,690 detail records, index
  targets and packaged hashes verified. MPs/ODS: 10 October; GP: 1 July; postcode:
  August 2026; PCON: July 2024.
- Real artifact: **64,699 files, 232.70 MB**, archive **22.87 MB**. Largest
  directory **5,718**, below Netlify's [54,000 limit](https://docs.netlify.com/deploy/deploy-overview/);
  v0.4's 64,040-flat-file directory exceeded it. Sharded hash paths preserve
  query links; schema-1 flat lookup remains supported for rollback.
- Initial shell/release gzip sum **13.47 KB**; lazy indexes **658.83 KB**; largest
  detail **621.79 KB raw**. Local real deep-link load **322 ms**, first organisation
  search **296 ms**: automation on loopback, not mobile network or human timings.
- [Full synthetic rehearsal](preview-evidence/synthetic-hosting-results.json):
  same 64,699 files; **221.70 MB** expanded (95.3% of real), **25.62 MB** archive
  (112.0%), indexes **1.02 MB gzip**, largest shard **10,000**. It stresses file
  count/upload bytes and index transfer; it does not replicate real payload shapes.
  Both complete candidates pass every hash/link check; repeat archive is identical;
  local extraction/verification took **34.61 / 38.13 s**. Protected serving,
  401/404/cache/query routes and pin **1 → 2 → 1**, including previous-session
  snapshot assets, pass. No provider upload/CDN test is claimed.
- [Browser evidence](preview-evidence/browser-results.json): Chromium 151,
  Firefox 153 and WebKit 26.5; four deep links and mobile/keyboard smoke pass.
  Chromium covers delayed startup/Escape/Tab, vacancy, inactive/unresolved RE6,
  partial/empty/missing data, clipboard fallback, 320/390px, 200/400% reflow and
  reduced motion. Nine axe 4.14 page/states have **0 violations / 0 incomplete**
  checks. Manual screenshot inspection passed. Gradient contrast uncertainty
  exposed low contrast; the existing dark teal now provides ≥5.37:1 for masthead
  small text ([contrast calculation](preview-evidence/masthead-contrast.json)). Accessibility-tree smoke is recorded; spoken screen-reader and
  native UI zoom are **not** completed or certified.

## Blockers and approval decisions

| Priority / owner | Required evidence or decision |
| --- | --- |
| **P0 — maintainer: rights/privacy** | Accept [source→output matrix](RIGHTS_AND_ATTRIBUTION_GATE.md), especially five NI postcode-derived assignments/LPS permission; clarify NHS historical-cache/24-hour clause and Parliament professional-name permission/privacy basis. Accept small aggregate policy and exact notices; choose code licence. No external real-data distribution before this gate. |
| **P0 — maintainer: host/audience/budget** | Approve an isolated **synthetic-only** provider trial, access model and spend cap. [Host comparison/runbook](CONTROLLED_PREVIEW_RUNBOOK.md) prefers GitHub immutable release assets + hash ledger, then sharded Netlify if access is suitable; private R2/Worker is the alternative. Netlify Pro starts $20/month; scenario traffic about 18 credits. R2 storage/read/write scenario fits free allowances, with Worker/Access costs to confirm. |
| **P1 — maintainer/test facilitator: usability/accessibility** | Run [5–8 person protocol](USER_TEST_PROTOCOL.md), spoken screen-reader and native zoom checks. Proposed ≥80% completion/interpretation, zero critical misunderstanding/blocker are **unmeasured**. No recruitment/messages sent. |
| **P1 — release operator: external hosting** | After approval, verify actual upload/count acceptance, unauthorised access to every alias/origin/asset, cache/404/gzip/mobile transfer and whole-release rollback. Local Basic auth is a rehearsal, not an approved remote security design. |

Post-preview order: fix critical trial misinterpretations/accessibility first;
then search-name/place friction, visible current-snapshot refresh policy, and
larger index/retained-snapshot optimisation if measurements justify it. Opt-out,
dementia/frailty and Issue #7 remain separate work.

## Public-beta release approval template — unsigned

```text
Maintainer / UTC date:
Reviewed PR head; site/source-build commit; archive/release/snapshot SHA:
Rights: source matrix accepted; LPS/ONS/NHS/Parliament evidence references:
Code licence, attribution text/year, small aggregate/privacy policy accepted:
Audience / historical or current status / source refresh owner and schedule:
Human trial outcomes; critical issues resolved; spoken reader/native zoom evidence:
Host/site/account; access policy; tested aliases/origin denial; budget cap:
Immutable release asset/tag/commit and independently verified download digest:
Pinned candidate/deploy; prior good artifact; rollback operator and tested links:
Approved action, URL/audience and timing (preview versus public beta stated):
Production/public release authorised? YES / NO (default NO)
DNS or analytics changes separately authorised? YES / NO (default NO)
Maintainer signature / decision / unresolved conditions:
```

Approval of code/PR alone is not approval to distribute the candidate or deploy it.
