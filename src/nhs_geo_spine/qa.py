"""Hard validation and machine-readable quality reporting."""

import json
from collections import Counter
from pathlib import Path

import duckdb
import polars as pl


def assert_unique(df: pl.DataFrame, key: str) -> None:
    if df.get_column(key).n_unique() != df.height:
        raise ValueError(f"Duplicate canonical key: {key}")


def reconcile_counts(source: pl.DataFrame, output: pl.DataFrame) -> None:
    before = source.select(pl.col("patient_count").sum()).item()
    after = output.select(pl.col("patient_count").sum()).item()
    if before != after:
        raise ValueError(f"Patient counts do not reconcile: {before} != {after}")


def _sum(df: pl.DataFrame, column: str) -> int:
    return int(df.select(pl.col(column).sum()).item() or 0)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def create_provider_qa(organisations: pl.DataFrame, bridge: pl.DataFrame,
                       pcon_names: pl.DataFrame) -> dict:
    """Audit all ODS codes and the explicit RE6 operating relationships."""
    assert_unique(organisations, "org_code")
    assert_unique(bridge, "org_code")
    if organisations.height != bridge.height:
        raise ValueError("Expanded site bridge does not contain one row per ODS code")
    if set(organisations["org_code"].to_list()) != set(bridge["org_code"].to_list()):
        raise ValueError("Expanded site bridge has missing or extra ODS codes")
    valid_pcons = set(pcon_names["pcon24cd"].to_list())
    invalid = set(bridge["pcon24cd"].drop_nulls().to_list()) - valid_pcons
    if invalid:
        raise ValueError(f"Expanded site bridge contains invalid PCON24 codes: {sorted(invalid)[:5]}")
    methods = {"postcode_direct", "postcode_to_lsoa_then_best_fit", "unmapped"}
    if set(bridge["mapping_method"].to_list()) - methods:
        raise ValueError("Expanded site bridge has an undocumented mapping method")
    expected_parent_types = {
        "gp_branch_surgery": {"gp_practice"},
        "nhs_trust_site": {"nhs_trust"},
        "sub_icb_location_site": {"sub_icb_location", "sub_icb_reporting_entity",
                                  "commissioning_hub", "icb_commissioning_proxy",
                                  "former_clinical_commissioning_group"},
    }
    parent_lookup = organisations.select(
        pl.col("org_code").alias("parent_org_code"),
        pl.col("organisation_type").alias("parent_organisation_type"),
        pl.col("org_name").alias("parent_org_name"),
        pl.col("status").alias("parent_status"),
    )
    related = organisations.filter(pl.col("relationship_type") == "RE6").join(
        parent_lookup, on="parent_org_code", how="left", validate="m:1"
    )
    if related.filter(pl.col("parent_org_code").is_null()).height:
        raise ValueError("RE6 relationship has no parent ODS code")
    wrong = related.filter(~pl.col("organisation_type").is_in(list(expected_parent_types)))
    for child_type, allowed in expected_parent_types.items():
        mismatch = related.filter(
            (pl.col("organisation_type") == child_type)
            & pl.col("parent_organisation_type").is_not_null()
            & ~pl.col("parent_organisation_type").is_in(list(allowed))
        )
        if mismatch.height:
            wrong = pl.concat([wrong, mismatch])
    if wrong.height:
        raise ValueError(f"RE6 relationship targets an unexpected organisation type: {wrong['org_code'][0]}")
    unresolved = related.filter(pl.col("parent_organisation_type").is_null())
    parent_status_flags = related.filter(
        (pl.col("status") == "ACTIVE") & pl.col("parent_status").is_not_null()
        & (pl.col("parent_status") != "ACTIVE")
    ).select("org_code", "parent_org_code", "parent_status").sort("org_code").to_dicts()
    invalid_unresolved = unresolved.filter(pl.col("organisation_type") != "gp_branch_surgery")
    if invalid_unresolved.height:
        raise ValueError(f"Unresolved non-branch RE6 relationship: {invalid_unresolved['org_code'][0]}")
    joined = organisations.select("org_code", "organisation_type", "source_report").join(
        bridge.select("org_code", "status", "postcode_compact", "country_code",
                      "pcon24cd", "mapping_method", "unmapped_reason"),
        on="org_code", validate="1:1",
    )
    coverage = []
    for key, group in joined.group_by("organisation_type"):
        active = group.filter(pl.col("status") == "ACTIVE")
        valid = active.filter(pl.col("postcode_compact").is_not_null())
        valid_england = active.filter(
            pl.col("postcode_compact").is_not_null() & (pl.col("country_code") == "E92000001")
        )
        mapped_england = valid_england.filter(pl.col("pcon24cd").is_not_null())
        coverage.append({
            "organisation_type": key[0], "source_rows": group.height,
            "active_rows": active.height,
            "mapped_active_rows": active.filter(pl.col("pcon24cd").is_not_null()).height,
            "active_mapping_rate": (active.filter(pl.col("pcon24cd").is_not_null()).height
                                    / active.height if active.height else None),
            "valid_active_postcodes": valid.height,
            "mapped_valid_active_postcodes": valid.filter(pl.col("pcon24cd").is_not_null()).height,
            "valid_active_unknown_country_postcodes": valid.filter(
                pl.col("country_code").is_null()).height,
            "valid_active_england_postcodes": valid_england.height,
            "mapped_valid_active_england_postcodes": mapped_england.height,
            "valid_active_england_mapping_rate": (
                mapped_england.height / valid_england.height if valid_england.height else None
            ),
            "active_unmapped_by_reason": dict(sorted(Counter(
                active.filter(pl.col("pcon24cd").is_null())["unmapped_reason"].to_list()
            ).items(), key=lambda item: str(item[0]))),
        })
    coverage.sort(key=lambda row: row["organisation_type"])
    unresolved_rows = unresolved.select(
        "org_code", "organisation_type", "parent_org_code", "status"
    ).sort("org_code").to_dicts()
    return {
        "organisations": organisations.height,
        "bridge_rows": bridge.height,
        "active_unmapped_rows": joined.filter(
            (pl.col("status") == "ACTIVE") & pl.col("pcon24cd").is_null()).height,
        "by_organisation_type": coverage,
        "re6_relationships": related.height,
        "resolved_re6_relationships": related.height - unresolved.height,
        "unresolved_active_re6_relationships": unresolved.filter(
            pl.col("status") == "ACTIVE").height,
        "unresolved_re6_relationships": unresolved_rows,
        "active_child_parent_not_active": parent_status_flags,
    }


def attach_provider_qa(report: dict, organisations: pl.DataFrame, bridge: pl.DataFrame,
                       pcon_names: pl.DataFrame) -> dict:
    provider = create_provider_qa(organisations, bridge, pcon_names)
    report["provider_coverage"] = provider
    if (provider["active_unmapped_rows"] or provider["unresolved_active_re6_relationships"]
            or provider["active_child_parent_not_active"]):
        report["status"] = "pass_with_warnings"
    return report


def create_qa_report(
    lookup: pl.DataFrame, sites: pl.DataFrame, site_bridge: pl.DataFrame,
    patient_bridge: pl.DataFrame, unmapped: pl.DataFrame, patient_input_total: int,
    pcon_names: pl.DataFrame, postcode_counts: dict[str, int],
    practice_source_totals: pl.DataFrame | None = None,
) -> dict:
    """Check conservation and code integrity, then explain mapping gaps."""
    assert_unique(lookup, "lsoa21cd")
    assert_unique(sites, "org_code")
    assert_unique(site_bridge, "org_code")
    if sites.height != site_bridge.height:
        raise ValueError("Site bridge does not contain one row per ODS GP practice")
    pcon_codes = set(pcon_names["pcon24cd"].to_list())
    for table, column in ((lookup, "pcon24cd_best_fit"), (site_bridge, "pcon24cd"),
                          (patient_bridge, "pcon24cd")):
        actual = set(table.get_column(column).drop_nulls().to_list()) - {"UNMAPPED"}
        invalid = actual - pcon_codes
        if invalid:
            raise ValueError(f"Constituency codes outside July 2024 code set: {sorted(invalid)[:5]}")
    if patient_bridge.filter(pl.col("mapping_method") != "lsoa21_best_fit").height:
        raise ValueError("Patient bridge has a non-best-fit mapping method")
    methods = {"postcode_direct", "postcode_to_lsoa_then_best_fit", "unmapped"}
    if set(site_bridge["mapping_method"].to_list()) - methods:
        raise ValueError("Site bridge has an undocumented mapping method")
    patient_output_total = _sum(patient_bridge, "patient_count")
    if patient_input_total != patient_output_total:
        raise ValueError(f"Patient counts do not reconcile: {patient_input_total} != {patient_output_total}")
    if practice_source_totals is not None:
        assert_unique(practice_source_totals, "practice_code")
        aggregate_totals = patient_bridge.group_by("practice_code").agg(
            pl.col("patient_count").sum().alias("bridge_total")
        )
        comparison = practice_source_totals.join(
            aggregate_totals, on="practice_code", how="full", coalesce=True
        )
        differences = comparison.filter(
            pl.col("source_total").is_null() | pl.col("bridge_total").is_null()
            | (pl.col("source_total") != pl.col("bridge_total"))
        )
        if differences.height:
            raise ValueError(f"Patient counts fail practice-level reconciliation: {differences.row(0)}")
    unmapped_total = _sum(unmapped, "patient_count")
    bucket_total = _sum(patient_bridge.filter(pl.col("pcon24cd") == "UNMAPPED"), "patient_count")
    if unmapped_total != bucket_total:
        raise ValueError(f"Unmapped detail/buckets do not reconcile: {unmapped_total} != {bucket_total}")
    if patient_bridge.filter(pl.col("patient_count") < 0).height:
        raise ValueError("Negative aggregated patient count")
    shares = patient_bridge.group_by("practice_code").agg(pl.col("patient_share").sum())
    if shares.filter((pl.col("patient_share") - 1).abs() > 1e-9).height:
        raise ValueError("Patient shares do not sum to 1 for every practice")
    if patient_bridge.group_by("practice_code", "pcon24cd").len().filter(pl.col("len") > 1).height:
        raise ValueError("Duplicate practice/constituency bridge key")

    active_sites = site_bridge.filter(pl.col("status") == "ACTIVE")
    valid_active = active_sites.filter(pl.col("postcode_compact").is_not_null())
    mapped_valid = valid_active.filter(pl.col("pcon24cd").is_not_null())
    valid_active_england = valid_active.filter(pl.col("country_code") == "E92000001")
    mapped_valid_england = valid_active_england.filter(pl.col("pcon24cd").is_not_null())
    valid_active_unknown_country = valid_active.filter(pl.col("country_code").is_null())
    misses = valid_active.filter(pl.col("pcon24cd").is_null()).select(
        "org_code", "postcode_raw", "postcode", "country_code", "unmapped_reason"
    ).to_dicts()
    mapping_rate = mapped_valid.height / valid_active.height if valid_active.height else None
    england_rate_unavailable_reason = (
        "unknown_country_for_valid_active_gp_postcodes" if valid_active_unknown_country.height
        else "no_valid_active_england_gp_postcodes" if not valid_active_england.height
        else None
    )
    england_mapping_rate = (
        mapped_valid_england.height / valid_active_england.height
        if england_rate_unavailable_reason is None else None
    )
    unmapped_by_reason = unmapped.group_by("unmapped_reason").agg(
        pl.len().alias("rows"), pl.col("patient_count").sum().alias("patients")
    ).sort("unmapped_reason").to_dicts()
    unmapped_by_practice = unmapped.group_by("practice_code").agg(
        pl.col("patient_count").sum().alias("unmapped_patients")
    )
    practice_totals = patient_bridge.group_by("practice_code").agg(
        pl.col("patient_count").sum().alias("practice_total")
    )
    high_unmapped = unmapped_by_practice.join(practice_totals, on="practice_code").with_columns(
        (pl.col("unmapped_patients") / pl.col("practice_total")).alias("unmapped_share")
    ).filter(pl.col("unmapped_share") > 0.05).sort(
        ["unmapped_share", "practice_code"], descending=[True, False]
    ).to_dicts()
    address = site_bridge.select(pl.col("org_code").alias("practice_code"),
                                 pl.col("pcon24cd").alias("address_pcon24cd"))
    with_address = patient_bridge.filter(pl.col("pcon24cd") != "UNMAPPED").join(
        address, on="practice_code", how="left"
    )
    address_shares = with_address.group_by("practice_code", "address_pcon24cd").agg(
        pl.col("patient_share").filter(pl.col("pcon24cd") == pl.col("address_pcon24cd")).sum()
        .alias("address_patient_share"),
        pl.col("patient_share").filter(pl.col("pcon24cd") != pl.col("address_pcon24cd")).sum()
        .alias("outside_address_patient_share"),
    )
    low_address = address_shares.filter(
        pl.col("address_pcon24cd").is_not_null() & (pl.col("address_patient_share") < 0.10)
    ).sort(["address_patient_share", "practice_code"]).to_dicts()
    address_shares_known = address_shares.filter(pl.col("address_pcon24cd").is_not_null())
    outside_values = [float(value) for value in address_shares_known[
        "outside_address_patient_share"].to_list()]
    served = patient_bridge.filter(pl.col("pcon24cd") != "UNMAPPED").group_by("practice_code").agg(
        pl.col("pcon24cd").n_unique().alias("constituencies")
    )
    distribution = Counter("4+" if n >= 4 else str(n) for n in served["constituencies"].to_list())
    role_rates = []
    for role, group in site_bridge.group_by("org_role"):
        active = group.filter(pl.col("status") == "ACTIVE")
        role_rates.append({"org_role": role[0], "active_sites": active.height,
                           "mapped_active_sites": active.filter(pl.col("pcon24cd").is_not_null()).height})
    role_rates.sort(key=lambda row: row["org_role"])
    top_postcodes = active_sites.filter(pl.col("pcon24cd").is_null()).group_by("postcode_raw").agg(
        pl.len().alias("sites")
    ).sort(["sites", "postcode_raw"], descending=[True, False]).head(20).to_dicts()
    warnings = {
        "active_missing_postcode": active_sites.filter(pl.col("postcode_raw").str.strip_chars() == "")
        .select("org_code").to_series().to_list(),
        "active_invalid_postcode": active_sites.filter(
            pl.col("postcode_raw").str.strip_chars() != "")
        .filter(pl.col("postcode_compact").is_null()).select("org_code").to_series().to_list(),
        "active_postcode_absent_from_directory": [r["org_code"] for r in misses
            if r["unmapped_reason"] == "absent_from_postcode_directory"],
        "active_fallback_site_codes": active_sites.filter(
            pl.col("mapping_method") == "postcode_to_lsoa_then_best_fit"
        ).select("org_code").to_series().to_list(),
        "practice_over_5_percent_unmapped": high_unmapped,
        "address_constituency_under_10_percent": low_address,
    }
    return {
        "status": "pass_with_warnings" if any(warnings.values()) or misses
                  or valid_active_unknown_country.height else "pass",
        "hard_failures": [],
        "patient_reconciliation": {
            "source_total": patient_input_total, "bridge_total": patient_output_total,
            "mapped_total": patient_output_total - bucket_total,
            "unmapped_total": bucket_total, "difference": patient_input_total - patient_output_total,
            "per_practice_difference_count": 0 if practice_source_totals is not None else None,
            "unmapped_by_reason": unmapped_by_reason,
        },
        "site_mapping": {
            "source_sites": sites.height, "bridge_sites": site_bridge.height,
            "active_sites": active_sites.height,
            "mapped_active_sites": active_sites.filter(pl.col("pcon24cd").is_not_null()).height,
            "valid_active_gp_postcodes": valid_active.height,
            "mapped_valid_active_gp_postcodes": mapped_valid.height,
            "valid_active_gp_mapping_rate": mapping_rate,
            "valid_active_england_gp_postcodes": valid_active_england.height,
            "mapped_valid_active_england_gp_postcodes": mapped_valid_england.height,
            "valid_active_unknown_country_gp_postcodes": valid_active_unknown_country.height,
            "valid_active_england_gp_mapping_rate": england_mapping_rate,
            "england_rate_unavailable_reason": england_rate_unavailable_reason,
            "threshold_99_percent_met": (
                england_mapping_rate >= 0.99 if england_mapping_rate is not None else None
            ),
            "all_valid_active_misses": misses,
            "mapping_by_org_role": role_rates,
            "top_unmapped_postcodes": top_postcodes,
        },
        "patient_distribution": {
            "practice_count": practice_totals.height,
            "practices_by_constituency_count": {k: distribution.get(k, 0) for k in ("1", "2", "3", "4+")},
            "median_share_outside_address_pcon": _percentile(outside_values, 0.5),
            "p90_share_outside_address_pcon": _percentile(outside_values, 0.9),
        },
        "postcode_directory": postcode_counts,
        "warnings": warnings,
    }


def audit_outputs(processed_dir: Path) -> dict:
    """Recheck the saved bundle and its manifest without network access."""
    manifest = json.loads((processed_dir / "build_manifest.json").read_text(encoding="utf-8"))
    lookup = pl.read_parquet(processed_dir / "lsoa21_pcon24.parquet")
    sites = pl.read_parquet(processed_dir / "nhs_organisation_sites.parquet")
    site_bridge = pl.read_parquet(processed_dir / "nhs_org_to_pcon.parquet")
    patient_bridge = pl.read_parquet(processed_dir / "gp_practice_patient_pcon.parquet")
    unmapped = pl.read_parquet(processed_dir / "gp_patient_unmapped_lsoa.parquet")
    pcon = pl.read_parquet(processed_dir / "pcon24.parquet")
    practice_source_totals = pl.read_parquet(processed_dir / "gp_practice_source_totals.parquet")
    all_sites = pl.read_parquet(processed_dir / "all_nhs_organisation_sites.parquet")
    all_bridge = pl.read_parquet(processed_dir / "all_nhs_org_to_pcon.parquet")
    if all_sites.height != manifest["row_counts"]["all_ods_organisation_rows"]:
        raise ValueError("Expanded organisation row count differs from the build manifest")
    if all_bridge.height != manifest["row_counts"]["all_site_bridge_rows"]:
        raise ValueError("Expanded site bridge row count differs from the build manifest")
    if set(sites["org_code"].to_list()) != set(all_sites.filter(
        pl.col("organisation_type") == "gp_practice")["org_code"].to_list()):
        raise ValueError("Expanded dimension does not preserve the v0.1 GP practice set")
    postcode_path = str(processed_dir / "postcode_spine.parquet")
    with duckdb.connect() as connection:
        postcode_rows = connection.execute(
            "SELECT COUNT(*) FROM read_parquet(?)", [postcode_path]
        ).fetchone()[0]
        if postcode_rows != manifest["row_counts"]["postcode_directory"]["postcode_rows"]:
            raise ValueError("Postcode dimension row count differs from the build manifest")
        duplicate = connection.execute(
            """SELECT postcode_compact FROM read_parquet(?)
               WHERE postcode_compact IS NOT NULL
               GROUP BY postcode_compact HAVING COUNT(*) > 1 LIMIT 1""", [postcode_path]
        ).fetchone()
        if duplicate:
            raise ValueError(f"Duplicate canonical postcode in output: {duplicate[0]}")
        postcode_pcons = {row[0] for row in connection.execute(
            "SELECT DISTINCT pcon_code FROM read_parquet(?) WHERE pcon_code IS NOT NULL",
            [postcode_path],
        ).fetchall()}
        invalid = postcode_pcons - set(pcon["pcon24cd"].to_list())
        if invalid:
            raise ValueError(f"Postcode dimension has PCON outside July 2024 code set: {sorted(invalid)[:5]}")
    report = create_qa_report(
        lookup, sites, site_bridge, patient_bridge, unmapped,
        manifest["row_counts"]["gp_patient_source_total"], pcon,
        manifest["row_counts"]["postcode_directory"], practice_source_totals,
    )
    attach_provider_qa(report, all_sites, all_bridge, pcon)
    with duckdb.connect(str(processed_dir / "nhs_geography.duckdb"), read_only=True) as connection:
        for view in ("pcon_nhs_organisations", "pcon_provider_summary",
                     "organisation_pcon_profile"):
            count = connection.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
            saved = pl.read_parquet(processed_dir / f"{view}.parquet").height
            if count != saved or count != manifest["row_counts"][f"{view}_rows"]:
                raise ValueError(f"{view} row count differs between DuckDB, Parquet and manifest")
    return report
