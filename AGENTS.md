# AGENTS.md — NHS Geography Spine

## Mission

Implement a reproducible public-data pipeline mapping NHS organisations and GP registered populations to Westminster Parliamentary Constituencies.

Read `CODEX_HANDOFF.md` first, then `docs/SPEC.md`. Those files are the implementation contract.

## Non-negotiable design decisions

- Keep **organisation/site location** separate from **population served/catchment**.
- Prefer direct postcode -> July 2024 Westminster constituency mapping for organisation/site location.
- Use the official ONS LSOA 2021 -> July 2024 Westminster constituency best-fit lookup for England/Wales population aggregation.
- Never infer geography from organisation names or towns.
- Never silently discard unmapped postcodes, LSOAs, organisations or patient counts.
- Preserve source URL, source date/version, retrieval timestamp and SHA-256 for every downloaded input.
- MP membership is a separate time-varying dimension. Do not bake MP names into the core geography spine in the first pass.
- Do not commit large raw or processed datasets to Git.

## Working method

1. Inspect the repository and source documentation before editing.
2. Implement in small, reviewable commits.
3. Add or update tests with each transformation or source adapter.
4. Tests must run without network access by using tiny local fixtures.
5. Fail loudly and clearly when an upstream schema changes.
6. Keep builds deterministic and idempotent.
7. Run the acceptance checks before declaring the task complete.

## Acceptance commands

```bash
pytest -q
nhs-geo fetch
nhs-geo build
nhs-geo qa
```

## Completion bar

Do not stop at scaffolding. A successful first delivery should fetch real public sources, build the canonical outputs specified in `CODEX_HANDOFF.md`, create the DuckDB views, produce a machine-readable QA report, and document any remaining source/licensing or UK-nation extension gaps precisely.

If an upstream source cannot be accessed automatically, implement everything else, add a deterministic fixture/test for the intended adapter behaviour, and report the exact unresolved source or credential/manual action. Do not substitute guessed URLs or fabricated data.
