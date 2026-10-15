# Controlled preview / immutable release / rollback runbook

No host is configured or deployed by this PR. The executable tooling binds only
to loopback and uses fictional assets by default. A real external preview needs
the maintainer's signed rights/audience/host decision first. Public launch is a
separate approval. Do not link this repository to automatic Netlify publishing.

## Host comparison (official documentation checked 14 October 2026)

| Question | Netlify static artifact | Private Cloudflare R2 + Worker + Access |
| --- | --- | --- |
| Concrete capacity limit | [Deploy overview](https://docs.netlify.com/deploy/deploy-overview/) allows any overall file count but caps a directory at 54,000. v0.4's 64,040 flat organisation files fail this contract. v0.5 splits by first two code characters; real maximum 5,718. Every build and verifier checks the limit. | [R2 limits](https://developers.cloudflare.com/r2/platform/limits/) list unlimited object count/storage per bucket, 1,024-byte keys, 5 GiB single-part uploads. Treat paths as keys; no documented 54,000 directory limit. Do **not** substitute Cloudflare Pages or Workers Static Assets without their separate file-limit assessment. |
| Access restriction | [Project/password protection](https://docs.netlify.com/manage/security/secure-access-to-sites/password-protection/) differs by account plan. Credit-based Free/Personal private access is owner-only; Pro supports password/private team access. Must test every deploy alias, permalink and asset path while signed out. A URL/noindex is not access control. | Keep bucket private and public `r2.dev` access disabled. Worker is the only reader. Authenticate every shell/JSON/static path with Access; validate identity/token before bucket access and prevent direct origin bypass. [Workers domain access](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/) describes protecting a `workers.dev` endpoint; use an approved isolated endpoint without DNS changes. |
| Cache, routing, rollback | Whole-artifact atomic deploy and retained deploy permalinks; roll back the entire artifact. Use no-cache for shell/release and versioned data cache only **behind** auth. [Custom header docs](https://docs.netlify.com/manage/routing/headers/) support `_headers`; configure and verify after approval. Query routes need index.html, not SPA catch-all rewrites that return HTML for missing JSON. Retained old browser snapshot assets must be tested across deploys; framework skew protection is not assumed for this static app. | Upload a complete immutable archive prefix first, verify all object hashes, then change one current pointer atomically. Retain the previous prefix and switch the pointer back. Authenticated browser data can use private immutable caching; do not make protected responses publicly cacheable. New worker release and pointer changes also need rollback. |
| Cost scenario, not an account quote | [Published pricing](https://www.netlify.com/pricing/): Pro starts at $20/month with 3,000 credits; production publication costs 15 credits, traffic 20 credits/GB and 2 credits/10,000 requests. A 1,000-session trial at 0.8 MB and 10 requests/session is about **18 traffic credits** (16 bandwidth + 2 requests), before unrelated account usage. Preview deploy counts and transfer charges differ; confirm actual plan. No purchase or billing setting changed. | [R2 Standard](https://developers.cloudflare.com/r2/pricing/) includes 10 GB-month, 1 million writes and 10 million reads monthly; egress is free. Two current-sized candidates use about 0.47 GB and 129,398 objects, within those allowances if unused elsewhere. This trial's R2 component is projected **$0**; rounding/account-wide usage matter. [Worker pricing](https://developers.cloudflare.com/workers/platform/pricing/) has free allowances and a paid base of $5/month. Access eligibility/cost and operational ownership require confirmation. |
| Evidence still required | Actual synthetic upload/extraction, provider file acceptance, auth across aliases, cache headers, redirects/404 and mobile network behaviour. This PR has not made a Netlify API/CLI mutation. | Actual private bucket upload/hash readback, Worker/Access denial tests, alias/origin bypass checks, gzip/cache behaviour and pointer/Worker rollback. No Cloudflare resource has been created. |

**Recommendation:** retain Option C (GitHub Release assets + reviewed hash
manifest + pinned whole artifact) as the source of approved candidates, then
prefer the sharded Netlify artifact for a small controlled trial if the selected
account provides suitable access control. Choose R2/Worker if ongoing object
retention/scale outweighs its extra implementation/operating work. No provider
has been proven to accept this actual upload yet; approval of a synthetic
provider trial is the next measurable host decision.

[GitHub release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
allow up to 1,000 assets, each under 2 GiB: the approximately 23 MB real archive
fits comfortably. Ordinary release assets can be changed; use
[immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
and verify the commit/tag and asset digest. If permitted, draft the release,
attach the complete archive and reviewed ledger, then publish the immutable
release. This PR does not create a tag, release, upload or immutability setting.
GitHub Releases cannot serve this app directly as its static host. A public
repository release is public distribution and requires the rights gate.

## Reproduce the local fictional rehearsal

Run from repository root, using Python 3.12+ and the pinned dev dependencies:

```bash
python -m pip install '.[dev]'
npm ci --prefix web --ignore-scripts
cd web
npx playwright install chromium firefox webkit
cd ..
python -m web.rehearse --directory /tmp/lens-trial-small
python -m web.rehearse --directory /tmp/lens-trial-capacity --full-size
```

Use a **new empty directory** for each run; existing evidence is preserved. The
small CI run and full run use the same projection/layout/verifier/installer/server.
Full run has exactly 650 constituency + 64,040 organisation details with seeded
synthetic capacity padding; fictional distribution/compression is measured,
not claimed to reproduce every real payload shape. Both versions are generated,
archived, hash verified, locally extracted, installed and pinned 1 → 2 → 1.
Every index/relationship target and every generated asset hash is checked.
The repeat archive must match byte for byte. Failure/unsafe archive tests
preserve the prior good candidate/pointer.

`hosting-results.json` contains sizes, hashes, timings and outcomes. `browser/`
contains screenshots, axe results, accessibility-tree smoke and browser cases.
Random credentials stay in the server/test process environment, never in static
files or reports. HTTP Basic here is **loopback rehearsal only**, not a remote
authentication/TLS implementation. The server stops when the run ends.

The loopback host redirects root/deep links and unprefixed shell aliases to
`/candidates/<archive-sha>/...` with `no-store`. The document's scripts, relative
imports, release metadata, indexes and details then keep that candidate URL even
if the current pin changes during the initial load. No session cookies are
needed. Opening the root again selects the new pin; a copied candidate link
retains the earlier release. Unauthorised requests to either form are denied.
CI pauses the first release request after the script has loaded, switches between
a schema-1 flat-client fixture and schema 2 in both directions, then checks the
original detail loads without errors. A provider must prove equivalent binding
before any external trial; retaining old data alone does not prevent shell skew.

## Prepare an approved candidate without changing a host

1. Freeze reviewed code, sources and their hashes. Rerun pipeline/source QA,
   fixture tests, browser checks and rights matrix. Inspect all warnings and
   patient reconciliation difference (must be zero). Record clean code SHA.
2. Build accepted site into an owned output directory and package it outside
   that directory. Record source manifest, snapshot/asset-manifest, release JSON,
   archive SHA and byte size. Keep source inputs in controlled storage outside Git.
3. Use `python -m web.release verify --site <candidate>` to verify **every**
   packaged static/detail/index file and target, not only a sample. The reviewed
   archive SHA is the trusted input; a mutable `latest` pointer is insufficient.
4. Maintainer signs audience/rights/attribution/host/access/cost/rollback scope
   for that exact artifact. Synthetic external testing still needs separate
   host-mutation approval. The public-beta template is in the readiness report.
5. Only then upload the approved archive/ledger to the approved release location
   and download it again to verify the digest. Enable/verify release immutability
   where authorised. Never use `--clobber`, replace an existing asset, or build
   nightly against mutable source URLs on the host.

Local installation/pin examples (no provider operation):

```bash
python -m web.release install --archive candidate.tar.gz --sha256 REVIEWED_SHA --store /tmp/lens-store
python -m web.release pin --candidate REVIEWED_SHA --store /tmp/lens-store
```

## Host promotion and rollback after approval

- Stage the reviewed artifact behind the approved access gate. Before handing
  out its URL, test a fresh unauthorised browser against root, index, release,
  indexes, details, static files, old/new deploy aliases and any origin URL.
  All must deny access without leaking content. Confirm no credentials in frontend.
- Authenticated shell/release must revalidate; versioned data may be private and
  immutable. Verify gzip/JSON MIME, `nosniff`, noindex (supplemental only), absence
  of analytics, missing asset 404, and preservation of query-string deep links.
- Netlify: pin the complete successful protected deploy; retain prior deploy ID,
  artifact SHA and permalink. Roll back to that entire deploy, not a mixture of
  old `release.json` with new shell files. Test a browser already holding the
  old release as well as a fresh session. Preserve old data within the serving
  strategy or require use of the immutable deploy permalink.
- R2: retain both complete prefixes; hash readback before pointer switch. A
  single pointer selects a whole shell+release+snapshot. Roll back the pointer
  and Worker version if needed; verify no KV eventual-consistency assumption is
  being presented as an atomic global switch.
- Trigger rollback for factual/rights/access breach, missing hashes/assets,
  critical accessibility failure or reconciliation failure. Verify the same four
  deep links and source dates after rollback; record UTC time/operator/hashes.
- Production/public audience/DNS changes remain separate decisions even after a
  clean controlled trial. The local tool supports none of these external actions.
