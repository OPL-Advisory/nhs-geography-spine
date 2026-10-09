"""ONS 2021 LSOA to July 2024 constituency best-fit lookup."""

from pathlib import Path

import polars as pl

REQUIRED = (
    "LSOA21CD", "LSOA21NM", "LSOA21NMW", "PCON24CD", "PCON24NM",
    "PCON24NMW", "LAD21CD", "LAD21NM",
)


def read_lsoa_lookup(path: Path, source_version: str) -> pl.DataFrame:
    """Validate and normalise the official England/Wales best-fit table."""
    raw = pl.read_csv(path, infer_schema_length=0, null_values=[""])
    missing = sorted(set(REQUIRED) - set(raw.columns))
    if missing:
        raise ValueError(f"ONS lookup schema changed; missing columns: {missing}")
    table = raw.select(
        pl.col("LSOA21CD").alias("lsoa21cd"),
        pl.col("LSOA21NM").alias("lsoa21nm"),
        pl.col("LSOA21NMW").alias("lsoa21nmw"),
        pl.col("LAD21CD").alias("lad21cd"),
        pl.col("LAD21NM").alias("lad21nm"),
        pl.col("PCON24CD").alias("pcon24cd_best_fit"),
        pl.col("PCON24NM").alias("pcon24nm_best_fit"),
        pl.col("PCON24NMW").alias("pcon24nmw_best_fit"),
        pl.lit("ONS LSOA21 to PCON24 best fit").alias("source"),
        pl.lit(source_version).alias("source_version"),
    )
    if table.height == 0:
        raise ValueError("ONS lookup is empty")
    if table.get_column("lsoa21cd").n_unique() != table.height:
        raise ValueError("Duplicate LSOA21CD in official best-fit lookup")
    patterns = {
        "lsoa21cd": r"^[EW]010[0-9]{5}$",
        "pcon24cd_best_fit": r"^(E140|W070)[0-9]{5}$",
        "lad21cd": r"^(E0[6789]|W06)[0-9]{6}$",
    }
    for col, pattern in patterns.items():
        bad = table.filter(~pl.col(col).str.contains(pattern).fill_null(False))
        if bad.height:
            raise ValueError(f"ONS lookup has {bad.height} invalid {col} values; first: {bad[col][0]!r}")
    if table.select(pl.col("pcon24cd_best_fit").n_unique()).item() < 1:
        raise ValueError("ONS lookup has no constituency codes")
    return table.sort("lsoa21cd")
