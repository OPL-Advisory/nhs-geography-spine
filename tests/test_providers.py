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
from nhs_geo_spine.sources import (
    CORE_SOURCE_KEYS,
    fetch_sources,
    load_config,
    sha256_file,
    verified_sources,
)


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
            _report("C00002", "Mapped Sub ICB site", "SW1A 2AA", parent="00A"),
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
    assert pl.read_parquet(processed / "all_nhs_organisation_sites.parquet").height == 11
    assert pl.read_parquet(processed / "nhs_org_to_pcon.parquet").height == 3
    expanded = pl.read_parquet(processed / "all_nhs_organisation_sites.parquet")
    assert expanded.filter(pl.col("org_code") == "00A")["org_role"][0] == "RO98|RO319"
    assert expanded.filter(pl.col("org_code") == "R00001")["org_role"][0] == "RO197"
    assert report["provider_coverage"]["re6_relationships"] == 5
    assert report["provider_coverage"]["resolved_re6_relationships"] == 4
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
        assert connection.execute("SELECT COUNT(*) FROM pcon_nhs_organisations").fetchone()[0] == 9
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
        for organisation_type in ("integrated_care_board", "sub_icb_location",
                                  "sub_icb_location_site"):
            assert connection.execute("""
                SELECT organisation_codes FROM pcon_provider_summary
                WHERE pcon24cd = 'E14001063' AND organisation_type = ?
            """, [organisation_type]).fetchone()[0] == 1
        assert connection.execute("""
            SELECT parent_org_code, parent_org_name FROM pcon_nhs_organisations
            WHERE org_code = 'B00002'
        """).fetchone() == ("Y00001", None)
    assert audit_outputs(processed)["provider_coverage"]["organisations"] == 11
    for suffix in ("csv", "parquet"):
        path = processed / f"pcon_provider_summary.{suffix}"
        summary = pl.read_csv(path) if suffix == "csv" else pl.read_parquet(path)
        assert set(summary["organisation_type"].to_list()) >= {
            "integrated_care_board", "sub_icb_location", "sub_icb_location_site"
        }
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


def test_eccg_proxy_role_is_classified_and_combination_preserved(tmp_path: Path) -> None:
    config_path, raw = _provider_fixture(tmp_path)
    config = load_config(config_path)
    source = Path(config["ods_sub_icb_locations"].url.removeprefix("file://"))
    source.write_bytes(_csv([
        _report("00A", "Sub ICB location", "SW1A 2AA", non_primary="RO319"),
        _report("00P", "ICB commissioning proxy", "SW1A 2AA", non_primary="RO326"),
        _report("00B", "Location with proxy role", "SW1A 2AA", non_primary="RO319|RO326"),
        _report("00R", "Reporting entity", "SW1A 2AA", non_primary="RO327"),
        _report("00H", "Commissioning hub", "SW1A 2AA", non_primary="RO218"),
        _report("00C", "Former CCG", "SW1A 2AA"),
    ]))
    fetch_sources(config, raw)
    processed = tmp_path / "processed"
    build_pipeline(config_path, raw, processed, offline=True)
    rows = {row["org_code"]: row for row in pl.read_parquet(
        processed / "all_nhs_organisation_sites.parquet").to_dicts()}
    assert rows["00P"]["organisation_type"] == "icb_commissioning_proxy"
    assert rows["00P"]["non_primary_role_ids"] == "RO326"
    assert rows["00B"]["organisation_type"] == "sub_icb_location"
    assert rows["00B"]["non_primary_role_ids"] == "RO319|RO326"
    assert rows["00R"]["organisation_type"] == "sub_icb_reporting_entity"
    assert rows["00H"]["organisation_type"] == "commissioning_hub"
    assert rows["00C"]["organisation_type"] == "former_clinical_commissioning_group"


def test_eccgsite_accepts_ro326_only_parent(tmp_path: Path) -> None:
    config_path, raw = _provider_fixture(tmp_path)
    config = load_config(config_path)
    unit_source = Path(config["ods_sub_icb_locations"].url.removeprefix("file://"))
    unit_source.write_bytes(_csv([
        _report("00A", "Sub ICB location", "SW1A 2AA", non_primary="RO319"),
        _report("00P", "ICB commissioning proxy", "SW1A 2AA", non_primary="RO326"),
    ]))
    site_source = Path(config["ods_sub_icb_sites"].url.removeprefix("file://"))
    site_source.write_bytes(_csv([
        _report("C00001", "Sub ICB site", "SW1A 2AC", parent="00A"),
        _report("C00002", "Proxy operated site", "SW1A 2AA", parent="00P"),
    ]))
    fetch_sources(config, raw)
    processed = tmp_path / "processed"
    report = build_pipeline(config_path, raw, processed, offline=True)
    assert report["provider_coverage"]["resolved_re6_relationships"] == 4
    with duckdb.connect(str(processed / "nhs_geography.duckdb"), read_only=True) as connection:
        assert connection.execute("""
            SELECT parent_org_code, parent_org_name, parent_organisation_type,
                   relationship_type
            FROM pcon_nhs_organisations WHERE org_code = 'C00002'
        """).fetchone() == ("00P", "ICB commissioning proxy", "icb_commissioning_proxy", "RE6")


@pytest.mark.parametrize("source_key,code,organisation_type,report_name", [
    ("ods_gp_branches", "B00003", "gp_branch_surgery", "ebranchs"),
    ("ods_nhs_trust_sites", "T00002", "nhs_trust_site", "ets"),
    ("ods_sub_icb_sites", "C00003", "sub_icb_location_site", "eccgsite"),
])
def test_optional_missing_re6_operator_is_reported_without_dropping_site(
    tmp_path: Path, source_key: str, code: str, organisation_type: str, report_name: str,
) -> None:
    config_path, raw = _provider_fixture(tmp_path)
    config = load_config(config_path)
    source = Path(config[source_key].url.removeprefix("file://"))
    row = _report(code, "Site without reported operator", "SW1A 2AA")
    if report_name != "ets":
        row[15], row[16] = "not-a-date", "not-a-date"
    source.write_bytes(source.read_bytes() + _csv([row]))
    fetch_sources(config, raw)
    processed = tmp_path / "processed"
    report = build_pipeline(config_path, raw, processed, offline=True)
    coverage = report["provider_coverage"]
    assert coverage["missing_active_re6_operator_rows"] == 1
    assert coverage["missing_re6_operator_rows"] == [{
        "org_code": code, "organisation_type": organisation_type,
        "source_report": report_name, "status": "ACTIVE",
    }]
    manifest = json.loads((processed / "build_manifest.json").read_text(encoding="utf-8"))
    assert manifest["qa_summary"]["provider_missing_active_re6_operators"] == 1
    site = pl.read_parquet(processed / "all_nhs_organisation_sites.parquet").filter(
        pl.col("org_code") == code
    ).row(0, named=True)
    assert all(site[field] is None for field in (
        "parent_org_code", "operating_org_code", "relationship_type",
        "relationship_start_date", "relationship_end_date",
    ))
    assert audit_outputs(processed)["provider_coverage"]["missing_re6_operator_rows"] == (
        coverage["missing_re6_operator_rows"]
    )
    with duckdb.connect(str(processed / "nhs_geography.duckdb"), read_only=True) as connection:
        assert connection.execute("""
            SELECT parent_org_code, relationship_type, relationship_start_date,
                   relationship_end_date
            FROM pcon_nhs_organisations WHERE org_code = ?
        """, [code]).fetchone() == (None, None, None, None)


def test_present_re6_operator_still_validates_relationship_dates(tmp_path: Path) -> None:
    config_path, raw = _provider_fixture(tmp_path)
    config = load_config(config_path)
    source = Path(config["ods_gp_branches"].url.removeprefix("file://"))
    row = _report("B00003", "Branch with invalid RE6 date", "SW1A 2AA", parent="A81001")
    row[15] = "not-a-date"
    source.write_bytes(source.read_bytes() + _csv([row]))
    fetch_sources(config, raw)
    with pytest.raises(ValueError, match="invalid YYYYMMDD date"):
        build_pipeline(config_path, raw, tmp_path / "processed", offline=True)


@pytest.mark.parametrize("role", ["RO999", "RO319|RO999", "RO319|"])
def test_eccg_unexpected_role_fails_loudly(tmp_path: Path, role: str) -> None:
    config_path, raw = _provider_fixture(tmp_path)
    config = load_config(config_path)
    source = Path(config["ods_sub_icb_locations"].url.removeprefix("file://"))
    source.write_bytes(_csv([_report("00X", "Unexpected role", "SW1A 2AA",
                                    non_primary=role)]))
    fetch_sources(config, raw)
    with pytest.raises(ValueError, match="eccg has (unexpected|malformed) non-primary roles.*00X"):
        build_pipeline(config_path, raw, tmp_path / "processed", offline=True)


def test_core_build_uses_only_configured_provenance_from_shared_cache(tmp_path: Path) -> None:
    full_config_path, raw = _provider_fixture(tmp_path)
    full_config = load_config(full_config_path)
    fetch_sources(full_config, raw)
    ledger_path = raw / "sources_manifest.json"
    full_ledger = ledger_path.read_bytes()
    core_path = tmp_path / "core-sources.yml"
    source_yaml = yaml.safe_load(full_config_path.read_text(encoding="utf-8"))
    core_path.write_text(yaml.safe_dump({key: source_yaml[key] for key in CORE_SOURCE_KEYS}),
                         encoding="utf-8")
    core_config = load_config(core_path)
    assert set(fetch_sources(core_config, raw)["sources"]) == set(CORE_SOURCE_KEYS)
    assert set(verified_sources(core_config, raw)["sources"]) == set(CORE_SOURCE_KEYS)
    processed = tmp_path / "processed"
    build_pipeline(core_path, raw, processed, offline=True)
    manifest = json.loads((processed / "build_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["sources"]) == set(CORE_SOURCE_KEYS)
    assert pl.read_parquet(processed / "all_nhs_organisation_sites.parquet").height == 3
    assert ledger_path.read_bytes() == full_ledger
    assert set(json.loads(full_ledger)["sources"]) == set(full_config)
