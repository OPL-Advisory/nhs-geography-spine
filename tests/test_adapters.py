"""Tiny, network-free checks for the public-source adapters."""

import csv
import io
import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from nhs_geo_spine.ingest_gp_patients import read_gp_patients
from nhs_geo_spine.ingest_ods import read_gp_practices
from nhs_geo_spine.sources import SOURCE_KEYS, load_config


def _csv(rows: list[list[str]]) -> bytes:
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode()


def test_default_config_identifies_all_verified_sources() -> None:
    config = load_config(Path("config/sources.yml"))
    assert set(config) == set(SOURCE_KEYS)
    assert all(source.url.startswith("https://") for source in config.values())
    assert all(source.source_version and source.source_date for source in config.values())


def test_ods_ro76_filter_retains_raw_role_and_postcode(tmp_path: Path) -> None:
    rows = []
    for code, role in (("A81001", "RO76"), ("A81002", "RO76|RO268"), ("A81003", "RO72")):
        row = [""] * 27
        row[0], row[1], row[9], row[10], row[12], row[25] = (
            code, "A practice", " sw1a 2aa ", "20200101", "ACTIVE", role
        )
        rows.append(row)
    path = tmp_path / "epraccur.csv"
    path.write_bytes(_csv(rows))
    practices, source_rows = read_gp_practices(path, "2026-10-09", "DSE")
    assert source_rows == 3
    assert practices["org_code"].to_list() == ["A81001", "A81002"]
    assert practices["org_role"].to_list() == ["RO76", "RO76|RO268"]
    assert practices["postcode_raw"][0] == " sw1a 2aa "
    assert practices["postcode"][0] == "SW1A 2AA"
    assert practices.schema["open_date"] == pl.Date
    assert practices.schema["close_date"] == pl.Date
    assert practices.schema["source_snapshot_date"] == pl.Date
    assert practices["open_date"][0] == date(2020, 1, 1)
    assert practices["source_snapshot_date"][0] == date(2026, 10, 9)


def test_gp_patient_adapter_rejects_wrong_sex_member(tmp_path: Path) -> None:
    archive = tmp_path / "patients.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("gp-reg-pat-prac-lsoa-all.csv", _csv([
            ["PUBLICATION", "EXTRACT_DATE", "PRACTICE_CODE", "LSOA_CODE", "SEX", "NUMBER_OF_PATIENTS"],
            ["GP_PRAC_PAT_LIST", "2026-07-01", "A81001", "E01000001", "MALE", "10"],
        ]))
    with pytest.raises(ValueError, match="SEX=ALL"):
        read_gp_patients(archive, "gp-reg-pat-prac-lsoa-all.csv", "2026-07-01")
