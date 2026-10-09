"""Offline source and end-to-end tests using tiny official-layout fixtures."""

import csv
import json
import zipfile
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml
from typer.testing import CliRunner

from nhs_geo_spine.build import app, build_pipeline
from nhs_geo_spine.ingest_ons import read_lsoa_lookup
from nhs_geo_spine.ingest_postcodes import read_pcon_names, write_postcodes
from nhs_geo_spine.qa import audit_outputs, create_qa_report
from nhs_geo_spine.sources import fetch_sources, load_config, sha256_file
from nhs_geo_spine.transform import aggregate_gp_patients


def _ods(code: str, postcode: str) -> list[str]:
    row = [""] * 27
    row[0], row[1], row[9], row[10], row[12], row[25] = (
        code, f"Practice {code}", postcode, "20200101", "ACTIVE", "RO76"
    )
    return row


def _postcode(postcode: str, pcon: str, lsoa: str) -> list[str]:
    row = [""] * 49
    row[0], row[1], row[2], row[8], row[12], row[33] = (
        postcode, postcode, "202001", "E09000001", "E92000001", pcon
    )
    row[36], row[37], row[46], row[47], row[48] = (
        "530000", "180000", "E00000001", lsoa, "E02000001"
    )
    return row


def _csv(rows: list[list[str]]) -> bytes:
    import io
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue().encode()


def _fixture_sources(tmp_path: Path) -> tuple[Path, Path]:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    ons = source_dir / "ons.csv"
    ons.write_bytes(_csv([
        ["LSOA21CD", "LSOA21NM", "LSOA21NMW", "PCON24CD", "PCON24NM", "PCON24NMW", "LAD21CD", "LAD21NM"],
        ["E01000001", "Area 1", "", "E14001063", "Aldershot", "", "E09000001", "City"],
        ["E01000002", "Area 2", "", "E14001064", "Aldridge-Brownhills", "", "E09000001", "City"],
    ]))
    ods = source_dir / "ods.csv"
    ods.write_bytes(_csv([_ods("A81001", "SW1A 2AA"), _ods("A81002", "SW1A 2AB"),
                          _ods("A81003", "SW1A 2AC")]))
    nhspd = source_dir / "nhspd.zip"
    with zipfile.ZipFile(nhspd, "w") as archive:
        archive.writestr("Data/nhg26aug.csv", _csv([
            _postcode("SW1A 2AA", "E14001063", "E01000001"),
            _postcode("SW1A 2AB", "", "E01000002"),
            _postcode("SW1A 2AC", "", ""),
        ]))
        archive.writestr("Documents/pcon.csv", _csv([
            ["PCON24CD", "PCON24NM", "PCON24NMW"],
            ["E14001063", "Aldershot", ""], ["E14001064", "Aldridge-Brownhills", ""],
        ]))
    patients = source_dir / "patients.zip"
    with zipfile.ZipFile(patients, "w") as archive:
        archive.writestr("gp-reg-pat-prac-lsoa-all.csv", _csv([
            ["PUBLICATION", "EXTRACT_DATE", "PRACTICE_CODE", "PRACTICE_NAME", "LSOA_CODE", "SEX", "NUMBER_OF_PATIENTS"],
            ["GP_PRAC_PAT_LIST", "2026-07-01", "A81001", "P1", "E01000001", "ALL", "10"],
            ["GP_PRAC_PAT_LIST", "2026-07-01", "A81001", "P1", "E01000002", "ALL", "5"],
            ["GP_PRAC_PAT_LIST", "2026-07-01", "A81001", "P1", "CLOSED", "ALL", "1"],
            ["GP_PRAC_PAT_LIST", "2026-07-01", "A81002", "P2", "E01000002", "ALL", "20"],
        ]))
    config = {
        "ons_lsoa21_pcon24": {"url": ons.as_uri(), "filename": "ons.csv", "source_version": "2024", "source_date": "2024-07-01", "publisher": "ONS"},
        "ods_gp_practices": {"url": ods.as_uri(), "filename": "ods.csv", "source_version": "DSE", "source_date": "retrieval", "publisher": "NHS"},
        "nhs_postcode_directory": {"url": nhspd.as_uri(), "filename": "nhspd.zip", "source_version": "August 2026", "source_date": "2026-08-31", "publisher": "ONS", "data_member": "Data/nhg26aug.csv", "pcon_member": "Documents/pcon.csv"},
        "gp_registered_patients_lsoa": {"url": patients.as_uri(), "filename": "patients.zip", "source_version": "July 2026", "source_date": "2026-07-01", "publisher": "NHS", "data_member": "gp-reg-pat-prac-lsoa-all.csv"},
    }
    config_path = tmp_path / "sources.yml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path, tmp_path / "raw"


def test_offline_build_and_qa(tmp_path: Path) -> None:
    config_path, raw = _fixture_sources(tmp_path)
    fetch_sources(load_config(config_path), raw)
    processed = tmp_path / "processed"
    report = build_pipeline(config_path, raw, processed, offline=True)
    assert report["patient_reconciliation"]["source_total"] == 36
    assert report["patient_reconciliation"]["unmapped_total"] == 1
    assert report["site_mapping"]["all_valid_active_misses"][0]["org_code"] == "A81003"
    manifest = json.loads((processed / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["qa_summary"]["valid_active_england_gp_mapping_rate"] == (
        report["site_mapping"]["valid_active_england_gp_mapping_rate"]
    )
    assert manifest["qa_summary"]["threshold_99_percent_met"] == (
        report["site_mapping"]["threshold_99_percent_met"]
    )
    sites = pl.read_parquet(processed / "nhs_org_to_pcon.parquet")
    assert sites["mapping_method"].to_list() == [
        "postcode_direct", "postcode_to_lsoa_then_best_fit", "unmapped"
    ]
    assert audit_outputs(processed)["patient_reconciliation"]["difference"] == 0
    with duckdb.connect(str(processed / "nhs_geography.duckdb"), read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM pcon_gp_patient_links").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM gp_practice_pcon_profile").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM pcon_nhs_organisations").fetchone()[0] == 2
        for table, fields in (
            ("dim_postcode", ("source_snapshot_date",)),
            ("dim_nhs_organisation_site", ("open_date", "close_date", "source_snapshot_date")),
            ("bridge_org_site_pcon", ("source_snapshot_date",)),
        ):
            for field in fields:
                column_type = connection.execute(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = ? AND column_name = ?", [table, field]
                ).fetchone()[0]
                assert column_type == "DATE"
    for filename, fields in (
        ("postcode_spine.parquet", ("source_snapshot_date",)),
        ("nhs_organisation_sites.parquet", ("open_date", "close_date", "source_snapshot_date")),
        ("nhs_org_to_pcon.parquet", ("source_snapshot_date",)),
    ):
        schema = pq.read_schema(processed / filename)
        for field in fields:
            assert schema.field(field).type == pa.date32()
    assert pl.read_parquet(processed / "postcode_spine.parquet")["source_snapshot_date"][0] == date(2026, 8, 31)
    assert (processed / "nhs_org_to_pcon.csv").exists()
    assert (processed / "gp_practice_patient_pcon.csv").exists()
    assert (processed / "gp_practice_source_totals.parquet").exists()


def test_duplicate_ons_key_fails(tmp_path: Path) -> None:
    config_path, _raw = _fixture_sources(tmp_path)
    config = load_config(config_path)
    path = Path(config["ons_lsoa21_pcon24"].url.removeprefix("file://"))
    content = path.read_text()
    path.write_text(content + content.splitlines()[1] + "\n")
    with pytest.raises(ValueError, match="Duplicate LSOA21CD"):
        read_lsoa_lookup(path, "2024")


def test_unmapped_reconciles_by_reason() -> None:
    patient = pl.DataFrame({
        "practice_code": ["A81001"] * 4,
        "lsoa21cd": ["E01000001", "E01099999", "CLOSED", "S01019652"],
        "patient_count": [10, 3, 2, 1],
        "lsoa_source_period": ["2026-07-01"] * 4,
    })
    lookup = pl.DataFrame({"lsoa21cd": ["E01000001"],
                           "pcon24cd_best_fit": ["E14001063"],
                           "pcon24nm_best_fit": ["Aldershot"]})
    bridge, detail = aggregate_gp_patients(patient, lookup, "July 2026")
    assert bridge["patient_count"].sum() == 16
    assert bridge.filter(pl.col("pcon24cd") == "UNMAPPED")["patient_count"][0] == 6
    assert set(detail["unmapped_reason"].to_list()) == {
        "missing_ons_lookup", "source_closed", "outside_ew_lsoa21_coverage"
    }


def test_cli_fetch_build_qa_from_clean_cache(tmp_path: Path) -> None:
    config, raw = _fixture_sources(tmp_path)
    processed = tmp_path / "processed"
    runner = CliRunner()
    for arguments in (
        ["fetch", "--config", str(config), "--raw-dir", str(raw)],
        ["build", "--offline", "--config", str(config), "--raw-dir", str(raw),
         "--processed-dir", str(processed)],
        ["qa", "--processed-dir", str(processed)],
    ):
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0, result.output


def test_nhspd_invalid_2021_geography_fails(tmp_path: Path) -> None:
    config_path, _raw = _fixture_sources(tmp_path)
    config = load_config(config_path)
    original = Path(config["nhs_postcode_directory"].url.removeprefix("file://"))
    altered = tmp_path / "bad_nhspd.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(altered, "w") as target:
        row = _postcode("SW1A 2AA", "E14001063", "E01100001")
        target.writestr("Data/nhg26aug.csv", _csv([row]))
        target.writestr("Documents/pcon.csv", source.read("Documents/pcon.csv"))
    pcon = read_pcon_names(altered, "Documents/pcon.csv")
    with pytest.raises(ValueError, match="invalid LSOA21"):
        write_postcodes(altered, "Data/nhg26aug.csv", tmp_path / "postcodes.parquet",
                        pcon, "2026-08-19", "August 2026")


def test_qa_catches_practice_shift_even_when_global_total_matches(tmp_path: Path) -> None:
    config, raw = _fixture_sources(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    build_pipeline(config, raw, processed, offline=True)
    path = processed / "gp_practice_patient_pcon.parquet"
    bridge = pl.read_parquet(path)
    counts = bridge["patient_count"].to_list()
    counts[0] += 1
    counts[-1] -= 1
    bridge.with_columns(pl.Series("patient_count", counts)).write_parquet(path)
    with pytest.raises(ValueError, match="practice-level reconciliation"):
        audit_outputs(processed)


@pytest.mark.parametrize("fault, message", [
    ("not_zip", "expected ZIP"),
    ("missing_member", "missing expected member"),
    ("bad_crc", "integrity check"),
])
def test_fetch_rejects_bad_zip_before_caching(tmp_path: Path, fault: str, message: str) -> None:
    config_path, raw = _fixture_sources(tmp_path)
    source = load_config(config_path)["nhs_postcode_directory"]
    archive_path = Path(source.url.removeprefix("file://"))
    if fault == "not_zip":
        archive_path.write_bytes(b"not an archive")
    elif fault == "missing_member":
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(source.data_member, b"example")
    else:
        archive_path.write_bytes(archive_path.read_bytes().replace(b"SW1A 2AA", b"SW1A 2AZ", 1))
    with pytest.raises(ValueError, match=message):
        fetch_sources(load_config(config_path), raw)
    assert not (raw / source.filename).exists()
    ledger = json.loads((raw / "sources_manifest.json").read_text(encoding="utf-8"))
    assert "nhs_postcode_directory" not in ledger["sources"]


def test_fetch_revalidates_cached_zip(tmp_path: Path) -> None:
    config_path, raw = _fixture_sources(tmp_path)
    config = load_config(config_path)
    fetch_sources(config, raw)
    archive = raw / config["nhs_postcode_directory"].filename
    archive.write_bytes(b"previously cached invalid archive")
    ledger_path = raw / "sources_manifest.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["sources"]["nhs_postcode_directory"]["sha256"] = sha256_file(archive)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="expected ZIP"):
        fetch_sources(config, raw)


def test_99_percent_flag_uses_england_rate_when_wales_lifts_combined_rate() -> None:
    england = "E00001"
    welsh = [f"W{i:05d}" for i in range(100)]
    sites = pl.DataFrame({"org_code": [england, *welsh]})
    bridge = pl.DataFrame({
        "org_code": [england, *welsh],
        "status": ["ACTIVE"] * 101,
        "postcode_compact": ["SW1A2AA"] + [f"CF101{i:02d}" for i in range(100)],
        "postcode_raw": ["SW1A 2AA"] + ["CF10 1AA"] * 100,
        "postcode": ["SW1A 2AA"] + ["CF10 1AA"] * 100,
        "country_code": ["E92000001"] + ["W92000004"] * 100,
        "pcon24cd": [None] + ["W07000001"] * 100,
        "mapping_method": ["unmapped"] + ["postcode_direct"] * 100,
        "unmapped_reason": ["no_direct_or_lsoa_geography"] + [None] * 100,
        "org_role": ["RO76"] * 101,
    })
    patient = pl.DataFrame({
        "practice_code": [england], "pcon24cd": ["E14001063"],
        "patient_count": [10], "patient_share": [1.0],
        "mapping_method": ["lsoa21_best_fit"],
    })
    unmapped = pl.DataFrame(schema={
        "practice_code": pl.String, "patient_count": pl.Int64, "unmapped_reason": pl.String,
    })
    report = create_qa_report(
        pl.DataFrame({"lsoa21cd": ["E01000001"], "pcon24cd_best_fit": ["E14001063"]}),
        sites, bridge, patient, unmapped, 10,
        pl.DataFrame({"pcon24cd": ["E14001063", "W07000001"]}), {},
    )
    assert report["site_mapping"]["valid_active_gp_mapping_rate"] > 0.99
    assert report["site_mapping"]["valid_active_england_gp_mapping_rate"] == 0.0
    assert report["site_mapping"]["threshold_99_percent_met"] is False
