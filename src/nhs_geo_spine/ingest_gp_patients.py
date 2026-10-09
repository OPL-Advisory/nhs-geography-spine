"""NHS England quarterly GP registrations by 2021 LSOA."""

import zipfile
from io import BytesIO
from pathlib import Path

import polars as pl

REQUIRED = (
    "PUBLICATION", "EXTRACT_DATE", "PRACTICE_CODE", "LSOA_CODE", "SEX", "NUMBER_OF_PATIENTS",
)


def read_gp_patients(archive_path: Path, member: str, expected_period: str) -> pl.DataFrame:
    """Read only the ALL persons file, preserving non-E/W codes for unmapped QA."""
    with zipfile.ZipFile(archive_path) as archive:
        if member not in archive.namelist():
            raise ValueError(f"GP patient archive missing expected member {member}")
        raw = pl.read_csv(BytesIO(archive.read(member)), infer_schema_length=0,
                          schema_overrides={"NUMBER_OF_PATIENTS": pl.Int64})
    missing = sorted(set(REQUIRED) - set(raw.columns))
    if missing:
        raise ValueError(f"GP patient CSV schema changed; missing columns: {missing}")
    if raw.is_empty():
        raise ValueError("GP patient all-persons file is empty")
    if raw.filter(pl.col("SEX") != "ALL").height:
        raise ValueError("GP patient member includes records other than SEX=ALL")
    if raw.get_column("EXTRACT_DATE").n_unique() != 1 or raw["EXTRACT_DATE"][0] != expected_period:
        raise ValueError(f"GP patient period differs from configured {expected_period}")
    if raw.filter(pl.col("PRACTICE_CODE").is_null() | ~pl.col("PRACTICE_CODE").str.contains(r"^[A-Z0-9]{6,12}$")).height:
        raise ValueError("GP patient file has invalid practice codes")
    valid_lsoa = r"^(?:[EWS]010[0-9]{5}|N210[0-9]{5}|[LM]99999999|EMPTY|CLOSED)$"
    bad = raw.filter(pl.col("LSOA_CODE").is_null() | ~pl.col("LSOA_CODE").str.contains(valid_lsoa))
    if bad.height:
        raise ValueError(f"GP patient file has unexpected LSOA_CODE: {bad['LSOA_CODE'][0]!r}")
    if raw.filter(pl.col("NUMBER_OF_PATIENTS").is_null() | (pl.col("NUMBER_OF_PATIENTS") < 0)).height:
        raise ValueError("GP patient file has null/negative patient counts")
    table = raw.select(
        pl.col("PRACTICE_CODE").alias("practice_code"),
        pl.col("LSOA_CODE").alias("lsoa21cd"),
        pl.col("NUMBER_OF_PATIENTS").alias("patient_count"),
        pl.col("EXTRACT_DATE").alias("lsoa_source_period"),
    )
    duplicates = table.group_by(["practice_code", "lsoa21cd"]).len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError(f"GP patient file has duplicate practice/LSOA keys: {duplicates.row(0)}")
    return table.sort(["practice_code", "lsoa21cd"])
