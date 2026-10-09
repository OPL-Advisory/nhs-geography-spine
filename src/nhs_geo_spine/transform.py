import polars as pl


def map_lsoa_to_pcon(patient_lsoa: pl.DataFrame, lookup: pl.DataFrame) -> pl.DataFrame:
    """Aggregate practice/LSOA patient counts to practice/PCON, retaining unmapped rows."""
    joined = patient_lsoa.join(
        lookup.select(["LSOA21CD", "PCON24CD", "PCON24NM"]),
        on="LSOA21CD",
        how="left",
    ).with_columns(
        pl.col("PCON24CD").fill_null("UNMAPPED"),
        pl.col("PCON24NM").fill_null("Unmapped"),
    )
    out = joined.group_by(["practice_code", "PCON24CD", "PCON24NM"]).agg(
        pl.col("patient_count").sum()
    )
    totals = out.group_by("practice_code").agg(
        pl.col("patient_count").sum().alias("practice_total")
    )
    return out.join(totals, on="practice_code").with_columns(
        (pl.col("patient_count") / pl.col("practice_total")).alias("patient_share"),
        pl.lit("lsoa21_best_fit").alias("mapping_method"),
    )
