"""Offline v0.2 report fixtures and parliamentary-view regressions."""

import json
from pathlib import Path

import duckdb
import polars as pl
import pytest
import yaml
from test_pipeline import _csv, _fixture_sources
from typer.testing import CliRunner

from nhs_geo_spine.build import app, build_pipeline
from nhs_geo_spine.ingest_providers import read_icb_codes
from nhs_geo_spine.qa import audit_outputs, create_provider_qa
from nhs_geo_spine.sources import fetch_sources, load_config, sha256_file


def _report(code: str, name: str, postcode: str, *, parent: str = "",
            non_primary: str = "", closed: str = "") -> list[str]:
    row = [""] * 27
    row[0], row[1], row[9], row[10], row[11] = code, name, postcode, "20200101", closed
    row[13], row[14], row[15] = non_primary, parent, "20200101" if parent else ""
    return row


def _provider_fixture(tmp_path: Path) -> tuple[Path, Path]:
    config_path, raw = _fixture_sources(tmp_path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    source_dir = tmp_path / "source"
    reports = {
        "ods_gp_branches": ("ebranchs", [
            _report("B00001", "Branch", "SW1A 2AA", parent="A81001"),
            _report("B00002", "Unresolved branch", "SW1A 2AA", parent="Y00001"),
        ]),
        "ods_nhs_trusts": ("etr", [
            _report("R00001", "Example NHS Foundation Trust", "SW1A 2AB"),
        ]),
        "ods_nhs_trust_sites": ("ets", [
            _report("T00001", "Trust site", "SW1A 2AA", parent="R00001", non_primary="RO31"),
        ]),
        "ods_other": ("eother", [
            _report("QAA", "Example ICB", "SW1A 2AA"),
            _report("X00001", "Other statutory organisation", "SW1A 2AA"),
        ]),
        "ods_sub_icb_locations": ("eccg", [
            _report("00A", "Example sub ICB location", "SW1A 2AA", non_primary="RO319"),
        ]),
        "ods_sub_icb_sites": ("eccgsite", [
            _report("C00001", "Sub ICB site", "SW1A 2AC", parent="00A"),
        ]),
    }
    for key, (report, rows) in reports.items():
        source = source_dir / f"{report}.csv"
        source.write_bytes(_csv(rows))
        config[key] = {"url": source.as_uri(), "filename": source.name,
                       "source_version": report, "source_date": "retrieval", "publisher": "NHS ODS"}
    icb = source_dir / "icb.csv"
    icb.write_bytes(_csv([
        ["ICB26CD", "ICB26CDH", "ICB26NM", "ObjectId"],
        ["E54000001", "QAA", "Example ICB", "1"],
    ]))
    config["ons_icb26_codes"] = {"url": icb.as_uri(), "filename": "icb.csv",
                                 "source_version": "April 2026", "source_date": "2026-04-01",
                                 "publisher": "ONS"}
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path, raw


def test_provider_build_preserves_gp_and_patient_outputs(tmp_path: Path) -> None:
    config, raw = _provider_fixture(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    report = build_pipeline(config, raw, processed, offline=True)
    assert report["patient_reconciliation"]["source_total"] == 36
    assert report["patient_reconciliation"]["difference"] == 0
    assert pl.read_parquet(processed / "nhs_organisation_sites.parquet").height == 3
    assert pl.read_parquet(processed / "all_nhs_organisation_sites.parquet").height == 10
    assert pl.read_parquet(processed / "nhs_org_to_pcon.parquet").height == 3
    expanded = pl.read_parquet(processed / "all_nhs_organisation_sites.parquet")
    assert expanded.filter(pl.col("org_code") == "00A")["org_role"][0] == "RO98|RO319"
    assert expanded.filter(pl.col("org_code") == "R00001")["org_role"][0] == "RO197"
    assert report["provider_coverage"]["re6_relationships"] == 4
    assert report["provider_coverage"]["resolved_re6_relationships"] == 3
    assert report["provider_coverage"]["unresolved_re6_relationships"][0]["parent_org_code"] == "Y00001"
    coverage = {row["organisation_type"]: row for row in report["provider_coverage"][
        "by_organisation_type"]}
    assert coverage["nhs_trust"]["valid_active_england_mapping_rate"] == 1.0
    assert coverage["sub_icb_location_site"]["active_unmapped_by_reason"] == {
        "no_direct_or_lsoa_geography": 1
    }
    manifest = json.loads((processed / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["row_counts"]["provider_reports"]["eother"] == {
        "source_rows": 2, "selected_rows": 1
    }
    assert manifest["sources"]["ons_icb26_codes"]["sha256"]
    with duckdb.connect(str(processed / "nhs_geography.duckdb"), read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM gp_practice_pcon_profile").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM pcon_gp_patient_links").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM pcon_nhs_organisations").fetchone()[0] == 8
        parent = connection.execute("""
            SELECT parent_org_code, parent_org_name, parent_organisation_type,
                   parent_address_pcon24cd, relationship_type, geography_basis
            FROM pcon_nhs_organisations WHERE org_code = 'T00001'
        """).fetchone()
        assert parent == ("R00001", "Example NHS Foundation Trust", "nhs_trust",
                          "E14001064", "RE6", "site_postcode")
        assert connection.execute("""
            SELECT COUNT(*) FROM pcon_nhs_organisations WHERE org_code = 'X00001'
        """).fetchone()[0] == 0
        assert connection.execute("""
            SELECT address_pcon24cd, unmapped_reason FROM organisation_pcon_profile
            WHERE org_code = 'C00001'
        """).fetchone() == (None, "no_direct_or_lsoa_geography")
        assert connection.execute("""
            SELECT organisation_codes FROM pcon_provider_summary
            WHERE pcon24cd = 'E14001063' AND organisation_type = 'gp_branch_surgery'
        """).fetchone()[0] == 2
        assert connection.execute("""
            SELECT parent_org_code, parent_org_name FROM pcon_nhs_organisations
            WHERE org_code = 'B00002'
        """).fetchone() == ("Y00001", None)
    assert audit_outputs(processed)["provider_coverage"]["organisations"] == 10
    exported = tmp_path / "exports"
    result = CliRunner().invoke(app, ["export", "--format", "csv", "--processed-dir",
                                     str(processed), "--output-dir", str(exported)])
    assert result.exit_code == 0, result.output
    assert (exported / "pcon_nhs_organisations.csv").exists()
    assert (exported / "pcon_provider_summary.csv").exists()
    assert (exported / "organisation_pcon_profile.csv").exists()


def test_provider_outputs_are_deterministic_for_fixed_source_bytes(tmp_path: Path) -> None:
    config, raw = _provider_fixture(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    build_pipeline(config, raw, processed, offline=True)
    names = ["all_nhs_organisation_sites.parquet", "all_nhs_org_to_pcon.parquet",
             "pcon_nhs_organisations.parquet", "pcon_provider_summary.csv",
             "organisation_pcon_profile.csv", "gp_practice_patient_pcon.parquet"]
    first = {name: sha256_file(processed / name) for name in names}
    build_pipeline(config, raw, processed, offline=True)
    assert {name: sha256_file(processed / name) for name in names} == first


def test_bad_provider_report_keeps_previous_bundle(tmp_path: Path) -> None:
    config_path, raw = _provider_fixture(tmp_path)
    config = load_config(config_path)
    fetch_sources(config, raw)
    processed = tmp_path / "processed"
    build_pipeline(config_path, raw, processed, offline=True)
    previous = {path.name: sha256_file(path) for path in processed.iterdir() if path.is_file()}
    source = Path(config["ods_nhs_trust_sites"].url.removeprefix("file://"))
    source.write_text('"bad","row"\n', encoding="utf-8")
    fetch_sources(config, raw, refresh=True)
    with pytest.raises(ValueError, match="ets schema changed"):
        build_pipeline(config_path, raw, processed, offline=True)
    assert {path.name: sha256_file(path) for path in processed.iterdir() if path.is_file()} == previous


def test_icb_code_schema_and_partial_provider_config_fail(tmp_path: Path) -> None:
    path = tmp_path / "icb.csv"
    path.write_text("name,code\nExample,QAA\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema changed"):
        read_icb_codes(path)
    (tmp_path / "other").mkdir()
    config_path, _raw = _fixture_sources(tmp_path / "other")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["ods_gp_branches"] = config["ods_gp_practices"]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="complete v0.2 source set"):
        load_config(config_path)


def test_provider_qa_rejects_wrong_operator_type(tmp_path: Path) -> None:
    config, raw = _provider_fixture(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    build_pipeline(config, raw, processed, offline=True)
    orgs = pl.read_parquet(processed / "all_nhs_organisation_sites.parquet").with_columns(
        pl.when(pl.col("org_code") == "T00001").then(pl.lit("A81001"))
        .otherwise(pl.col("parent_org_code")).alias("parent_org_code")
    )
    with pytest.raises(ValueError, match="unexpected organisation type"):
        create_provider_qa(orgs, pl.read_parquet(processed / "all_nhs_org_to_pcon.parquet"),
                           pl.read_parquet(processed / "pcon24.parquet"))
