import polars as pl


def assert_unique(df: pl.DataFrame, key: str) -> None:
    if df.select(pl.col(key).n_unique()).item() != df.height:
        raise ValueError(f"Duplicate canonical key: {key}")


def reconcile_counts(source: pl.DataFrame, output: pl.DataFrame) -> None:
    before = source.select(pl.col("patient_count").sum()).item()
    after = output.select(pl.col("patient_count").sum()).item()
    if before != after:
        raise ValueError(f"Patient counts do not reconcile: {before} != {after}")
