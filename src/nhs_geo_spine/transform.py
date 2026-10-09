"""Keep site-location and registered-population mappings separate."""

from pathlib import Path

import polars as pl


def aggregate_gp_patients(patient_lsoa: pl.DataFrame, lookup: pl.DataFrame,
                          source_version: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Best-fit LSOA allocation, with explicit practice-level unmapped buckets."""
    if lookup.get_column("lsoa21cd").n_unique() != lookup.height:
        raise ValueError("Duplicate LSOA21CD would multiply patient counts")
    joined = patient_lsoa.join(
        lookup.select("lsoa21cd", "pcon24cd_best_fit", "pcon24nm_best_fit"),
        on="lsoa21cd", how="left", validate="m:1",
    ).with_columns(
        pl.when(pl.col("pcon24cd_best_fit").is_not_null()).then(pl.lit(None, dtype=pl.String))
        .when(pl.col("lsoa21cd") == "EMPTY").then(pl.lit("source_empty"))
        .when(pl.col("lsoa21cd") == "CLOSED").then(pl.lit("source_closed"))
        .when(pl.col("lsoa21cd").str.contains(r"^[EW]010[0-9]{5}$"))
        .then(pl.lit("missing_ons_lookup"))
        .otherwise(pl.lit("outside_ew_lsoa21_coverage")).alias("unmapped_reason"),
    )
    unmapped = joined.filter(pl.col("unmapped_reason").is_not_null()).select(
        "practice_code", "lsoa21cd", "patient_count", "lsoa_source_period", "unmapped_reason"
    ).sort(["practice_code", "lsoa21cd"])
    bridge = joined.with_columns(
        pl.col("pcon24cd_best_fit").fill_null("UNMAPPED").alias("pcon24cd"),
        pl.col("pcon24nm_best_fit").fill_null("Unmapped").alias("pcon24nm"),
    ).group_by("practice_code", "pcon24cd", "pcon24nm", "lsoa_source_period").agg(
        pl.col("patient_count").sum()
    )
    totals = bridge.group_by("practice_code").agg(pl.col("patient_count").sum().alias("practice_total"))
    bridge = bridge.join(totals, on="practice_code", validate="m:1").with_columns(
        (pl.col("patient_count") / pl.col("practice_total")).alias("patient_share"),
        pl.lit("lsoa21_best_fit").alias("mapping_method"),
        pl.lit(source_version).alias("source_version"),
    ).select(
        "practice_code", "pcon24cd", "pcon24nm", "patient_count", "patient_share",
        "practice_total", "lsoa_source_period", "mapping_method", "source_version",
    ).sort("practice_code", "pcon24cd")
    before = patient_lsoa.select(pl.col("patient_count").sum()).item()
    after = bridge.select(pl.col("patient_count").sum()).item()
    if before != after:
        raise ValueError(f"Patient counts do not reconcile: {before} != {after}")
    return bridge, unmapped


def map_lsoa_to_pcon(patient_lsoa: pl.DataFrame, lookup: pl.DataFrame) -> pl.DataFrame:
    """Return the patient bridge from canonical lower-case columns."""
    return aggregate_gp_patients(patient_lsoa, lookup, "unspecified")[0]


def map_sites(sites: pl.DataFrame, postcode_parquet: Path, lookup: pl.DataFrame,
              postcode_version: str, lookup_version: str) -> pl.DataFrame:
    """Map ODS GP addresses by direct NHSPD PCON, then labelled LSOA fallback."""
    wanted = sites.get_column("postcode_compact").drop_nulls().to_list()
    postcodes = pl.scan_parquet(postcode_parquet).filter(
        pl.col("postcode_compact").is_in(wanted)
    ).select(
        "postcode_compact", pl.col("postcode").alias("directory_postcode"),
        pl.col("active").alias("postcode_directory_active"), "country_code", "lsoa_code",
        "pcon_code", "pcon_name",
    ).collect()
    if postcodes.get_column("postcode_compact").n_unique() != postcodes.height:
        raise ValueError("Duplicate canonical postcode in postcode dimension")
    mapped = sites.join(postcodes, on="postcode_compact", how="left", validate="m:1")
    mapped = mapped.join(
        lookup.select("lsoa21cd", "pcon24cd_best_fit", "pcon24nm_best_fit"),
        left_on="lsoa_code", right_on="lsoa21cd", how="left", validate="m:1",
    )
    method = (
        pl.when(pl.col("pcon_code").is_not_null()).then(pl.lit("postcode_direct"))
        .when(pl.col("pcon24cd_best_fit").is_not_null()).then(pl.lit("postcode_to_lsoa_then_best_fit"))
        .otherwise(pl.lit("unmapped"))
    )
    mapped = mapped.with_columns(
        method.alias("mapping_method"),
        pl.coalesce("pcon_code", "pcon24cd_best_fit").alias("pcon24cd"),
        pl.coalesce("pcon_name", "pcon24nm_best_fit").alias("pcon24nm"),
    ).with_columns(
        pl.when(pl.col("mapping_method") == "postcode_direct")
        .then(pl.when(pl.col("postcode_directory_active") == True)
              .then(pl.lit("direct_active_postcode"))
              .otherwise(pl.lit("direct_terminated_postcode")))
        .when(pl.col("mapping_method") == "postcode_to_lsoa_then_best_fit")
        .then(pl.lit("fallback_best_fit"))
        .otherwise(pl.lit("unmapped")).alias("mapping_quality"),
        pl.when(pl.col("mapping_method") != "unmapped").then(pl.lit(None, dtype=pl.String))
        .when(pl.col("postcode_raw").str.strip_chars() == "").then(pl.lit("missing_postcode"))
        .when(pl.col("postcode_compact").is_null()).then(pl.lit("invalid_postcode"))
        .when(pl.col("directory_postcode").is_null()).then(pl.lit("absent_from_postcode_directory"))
        .otherwise(pl.lit("no_direct_or_lsoa_geography")).alias("unmapped_reason"),
    )
    return mapped.select(
        "org_code", "site_code", "org_name", "org_role", "status", "postcode_raw", "postcode",
        "postcode_compact", "country_code", "postcode_directory_active", "pcon24cd", "pcon24nm",
        "mapping_method", "mapping_quality", "unmapped_reason", "source_snapshot_date",
        pl.lit(postcode_version).alias("postcode_source_version"),
        pl.lit(lookup_version).alias("lookup_source_version"),
    ).sort("org_code")
