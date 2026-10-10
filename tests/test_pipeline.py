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
        for table in ("dim_nhs_organisation_site", "bridge_org_site_pcon"):
            column_type = connection.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = ? AND column_name = 'site_code'", [table]
            ).fetchone()[0]
            assert column_type == "VARCHAR"
    for filename, fields in (
        ("postcode_spine.parquet", ("source_snapshot_date",)),
        ("nhs_organisation_sites.parquet", ("open_date", "close_date", "source_snapshot_date")),
        ("nhs_org_to_pcon.parquet", ("source_snapshot_date",)),
    ):
        schema = pq.read_schema(processed / filename)
        for field in fields:
            assert schema.field(field).type == pa.date32()
    for filename, field in (("nhs_organisation_sites.parquet", "parent_org_code"),
                            ("nhs_org_to_pcon.parquet", "site_code")):
        dtype = pq.read_schema(processed / filename).field(field).type
        assert pa.types.is_string(dtype) or pa.types.is_large_string(dtype)
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


@pytest.mark.parametrize("nested", ["export", "scratch/../export", "scratch/.."])
def test_nested_export_is_refused_without_side_effects_and_rebuilds(
    tmp_path: Path, nested: str,
) -> None:
    config, raw = _fixture_sources(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    build_pipeline(config, raw, processed, offline=True)
    before = {path.name: sha256_file(path) for path in processed.iterdir() if path.is_file()}
    entries = {path.name for path in processed.iterdir()}

    result = CliRunner().invoke(app, ["export", "--format", "csv", "--processed-dir",
                                     str(processed), "--output-dir", str(processed / nested)])
    assert result.exit_code != 0
    assert "output directory must be outside the processed bundle" in result.output
    assert {path.name for path in processed.iterdir()} == entries
    assert {path.name: sha256_file(path) for path in processed.iterdir() if path.is_file()} == before

    build_pipeline(config, raw, processed, offline=True)
    assert audit_outputs(processed)["patient_reconciliation"]["difference"] == 0


def test_export_to_separate_directory_still_works(tmp_path: Path) -> None:
    config, raw = _fixture_sources(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    build_pipeline(config, raw, processed, offline=True)
    destination = tmp_path / "exports"

    result = CliRunner().invoke(app, ["export", "--format", "csv", "--processed-dir",
                                     str(processed), "--output-dir", str(destination)])
    assert result.exit_code == 0, result.output
    for name in ("nhs_org_to_pcon.csv", "gp_practice_patient_pcon.csv"):
        assert sha256_file(destination / name) == sha256_file(processed / name)
    assert audit_outputs(processed)["patient_reconciliation"]["difference"] == 0


@pytest.mark.parametrize("already_owned", [False, True])
def test_build_refuses_custom_directory_with_unrelated_file(
    tmp_path: Path, already_owned: bool,
) -> None:
    config, raw = _fixture_sources(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "custom-output"
    if already_owned:
        build_pipeline(config, raw, processed, offline=True)
    else:
        processed.mkdir()
    unrelated = processed / "my-notes.txt"
    unrelated.write_text("keep this file\n", encoding="utf-8")
    previous = sha256_file(processed / "build_manifest.json") if already_owned else None
    with pytest.raises(ValueError, match="Unowned file|Unowned processed directory"):
        build_pipeline(config, raw, processed, offline=True)
    assert unrelated.read_text(encoding="utf-8") == "keep this file\n"
    if previous is not None:
        assert sha256_file(processed / "build_manifest.json") == previous
    assert not list(tmp_path.glob(".custom-output-build-*"))


def test_owned_custom_directory_rebuilds_complete_bundle(tmp_path: Path) -> None:
    config, raw = _fixture_sources(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "custom-output"
    build_pipeline(config, raw, processed, offline=True)
    marker = processed / ".nhs-geography-spine-output"
    assert marker.read_text(encoding="utf-8") == "nhs-geography-spine-output-v1\n"
    build_pipeline(config, raw, processed, offline=True)
    assert marker.exists()
    assert audit_outputs(processed)["patient_reconciliation"]["difference"] == 0
    assert not list(tmp_path.glob(".custom-output-build-*"))


def test_default_output_migrates_legacy_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, raw = _fixture_sources(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    (processed / ".gitkeep").write_text("\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("nhs_geo_spine.build._git_state",
                        lambda: {"git_sha": None, "git_dirty": None})
    build_pipeline(config, raw, Path("data/processed"), offline=True)
    assert (processed / ".nhs-geography-spine-output").exists()
    assert audit_outputs(processed)["patient_reconciliation"]["difference"] == 0


def test_build_refuses_current_directory_without_changing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, raw = _fixture_sources(tmp_path)
    fetch_sources(load_config(config), raw)
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("untouched\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="Unsafe processed directory"):
        build_pipeline(config, raw, Path("."), offline=True)
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"
    assert not list(tmp_path.glob("..-build-*"))


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


def test_failed_late_rebuild_keeps_previous_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path, raw = _fixture_sources(tmp_path)
    config = load_config(config_path)
    fetch_sources(config, raw)
    processed = tmp_path / "processed"
    build_pipeline(config_path, raw, processed, offline=True)
    previous = {path.name: sha256_file(path) for path in processed.iterdir() if path.is_file()}
    ods_source = Path(config["ods_gp_practices"].url.removeprefix("file://"))
    ods_source.write_text(ods_source.read_text().replace("Practice A81001", "Changed practice", 1))
    fetch_sources(config, raw, refresh=True)

    def fail_patient_adapter(*_args: object) -> None:
        raise ValueError("late patient adapter failure")

    monkeypatch.setattr("nhs_geo_spine.build.read_gp_patients", fail_patient_adapter)
    with pytest.raises(ValueError, match="late patient adapter failure"):
        build_pipeline(config_path, raw, processed, offline=True)
    assert {path.name: sha256_file(path) for path in processed.iterdir() if path.is_file()} == previous
    assert not list(processed.glob(".build-*"))


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


def test_unknown_postcode_country_makes_england_acceptance_indeterminate() -> None:
    sites = pl.DataFrame({"org_code": ["E00001", "X00001"]})
    bridge = pl.DataFrame({
        "org_code": ["E00001", "X00001"],
        "status": ["ACTIVE", "ACTIVE"],
        "postcode_compact": ["SW1A2AA", "SW1A2AB"],
        "postcode_raw": ["SW1A 2AA", "SW1A 2AB"],
        "postcode": ["SW1A 2AA", "SW1A 2AB"],
        "country_code": ["E92000001", None],
        "pcon24cd": ["E14001063", None],
        "mapping_method": ["postcode_direct", "unmapped"],
        "unmapped_reason": [None, "absent_from_postcode_directory"],
        "org_role": ["RO76|RO268", "RO76"],
    })
    patient = pl.DataFrame({
        "practice_code": ["E00001"], "pcon24cd": ["E14001063"],
        "patient_count": [10], "patient_share": [1.0],
        "mapping_method": ["lsoa21_best_fit"],
    })
    unmapped = pl.DataFrame(schema={
        "practice_code": pl.String, "patient_count": pl.Int64, "unmapped_reason": pl.String,
    })
    report = create_qa_report(
        pl.DataFrame({"lsoa21cd": ["E01000001"], "pcon24cd_best_fit": ["E14001063"]}),
        sites, bridge, patient, unmapped, 10,
        pl.DataFrame({"pcon24cd": ["E14001063"]}), {},
    )
    mapping = report["site_mapping"]
    assert mapping["valid_active_england_gp_postcodes"] == 1
    assert mapping["valid_active_unknown_country_gp_postcodes"] == 1
    assert mapping["valid_active_england_gp_mapping_rate"] is None
    assert mapping["england_rate_unavailable_reason"] == "unknown_country_for_valid_active_gp_postcodes"
    assert mapping["threshold_99_percent_met"] is None
    assert mapping["all_valid_active_misses"][0]["org_code"] == "X00001"
    assert [row["org_role"] for row in mapping["mapping_by_org_role"]] == ["RO76", "RO76|RO268"]


def test_outside_address_share_excludes_unmapped_patients() -> None:
    sites = pl.DataFrame({"org_code": ["E00001"]})
    site_bridge = pl.DataFrame({
        "org_code": ["E00001"], "status": ["ACTIVE"],
        "postcode_compact": ["SW1A2AA"], "postcode_raw": ["SW1A 2AA"],
        "postcode": ["SW1A 2AA"], "country_code": ["E92000001"],
        "pcon24cd": ["E14001063"], "mapping_method": ["postcode_direct"],
        "unmapped_reason": [None], "org_role": ["RO76"],
    })
    patient = pl.DataFrame({
        "practice_code": ["E00001", "E00001"],
        "pcon24cd": ["E14001063", "UNMAPPED"],
        "patient_count": [10, 90], "patient_share": [0.1, 0.9],
        "mapping_method": ["lsoa21_best_fit", "lsoa21_best_fit"],
    })
    unmapped = pl.DataFrame({
        "practice_code": ["E00001"], "patient_count": [90],
        "unmapped_reason": ["outside_ew_lsoa21_coverage"],
    })
    report = create_qa_report(
        pl.DataFrame({"lsoa21cd": ["E01000001"], "pcon24cd_best_fit": ["E14001063"]}),
        sites, site_bridge, patient, unmapped, 100,
        pl.DataFrame({"pcon24cd": ["E14001063"]}), {},
    )
    assert report["patient_reconciliation"]["unmapped_total"] == 90
    assert report["patient_distribution"]["median_share_outside_address_pcon"] == 0.0
    assert report["patient_distribution"]["p90_share_outside_address_pcon"] == 0.0


def test_top_unmapped_postcodes_breaks_count_ties_by_postcode() -> None:
    postcodes = [f"AA1 1A{chr(65 + i)}" for i in range(21)]
    reversed_postcodes = list(reversed(postcodes))
    codes = [f"S{i:05d}" for i in range(21)]
    sites = pl.DataFrame({"org_code": codes})
    site_bridge = pl.DataFrame({
        "org_code": codes, "status": ["ACTIVE"] * 21,
        "postcode_compact": [p.replace(" ", "") for p in reversed_postcodes],
        "postcode_raw": reversed_postcodes, "postcode": reversed_postcodes,
        "country_code": [None] * 21, "pcon24cd": [None] * 21,
        "mapping_method": ["unmapped"] * 21,
        "unmapped_reason": ["absent_from_postcode_directory"] * 21,
        "org_role": ["RO76"] * 21,
    })
    patient = pl.DataFrame({
        "practice_code": [codes[0]], "pcon24cd": ["E14001063"],
        "patient_count": [10], "patient_share": [1.0],
        "mapping_method": ["lsoa21_best_fit"],
    })
    unmapped = pl.DataFrame(schema={
        "practice_code": pl.String, "patient_count": pl.Int64, "unmapped_reason": pl.String,
    })
    report = create_qa_report(
        pl.DataFrame({"lsoa21cd": ["E01000001"], "pcon24cd_best_fit": ["E14001063"]}),
        sites, site_bridge, patient, unmapped, 10,
        pl.DataFrame({"pcon24cd": ["E14001063"]}), {},
    )
    assert [row["postcode_raw"] for row in report["site_mapping"]["top_unmapped_postcodes"]] == postcodes[:20]


def test_wales_only_build_reports_unavailable_england_rate(tmp_path: Path) -> None:
    config_path, raw = _fixture_sources(tmp_path)
    config = load_config(config_path)
    ods_path = Path(config["ods_gp_practices"].url.removeprefix("file://"))
    ods_path.write_bytes(_csv([_ods("W00001", "CF10 1AA")]))
    archive_path = Path(config["nhs_postcode_directory"].url.removeprefix("file://"))
    welsh_postcode = _postcode("CF10 1AA", "W07000001", "W01000001")
    welsh_postcode[12] = "W92000004"
    welsh_postcode[46] = "W00000001"
    welsh_postcode[48] = "W02000001"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Data/nhg26aug.csv", _csv([welsh_postcode]))
        archive.writestr("Documents/pcon.csv", _csv([
            ["PCON24CD", "PCON24NM", "PCON24NMW"],
            ["E14001063", "Aldershot", ""], ["E14001064", "Aldridge-Brownhills", ""],
            ["W07000001", "Example Wales", ""],
        ]))
    fetch_sources(config, raw)
    processed = tmp_path / "processed"
    result = CliRunner().invoke(app, [
        "build", "--offline", "--config", str(config_path), "--raw-dir", str(raw),
        "--processed-dir", str(processed),
    ])
    assert result.exit_code == 0, result.output
    assert "England valid active GP postcode mapping rate: unavailable" in result.output
    report = json.loads((processed / "qa_report.json").read_text(encoding="utf-8"))
    assert report["site_mapping"]["england_rate_unavailable_reason"] == "no_valid_active_england_gp_postcodes"
    assert report["site_mapping"]["threshold_99_percent_met"] is None
