"""Offline parliamentary member, relationship, CLI and publication regressions."""

import json
from pathlib import Path

import polars as pl
import pytest
import yaml
from test_providers import _provider_fixture
from typer.testing import CliRunner

from nhs_geo_spine.build import app, build_pipeline
from nhs_geo_spine.parliament import read_members
from nhs_geo_spine.qa import audit_outputs
from nhs_geo_spine.sources import fetch_sources, load_config, sha256_file


def _member_item(constituency_id: int, name: str, member_id: int | None) -> dict:
    member = None if member_id is None else {
        "value": {
            "id": member_id, "nameDisplayAs": f"Member {member_id}",
            "latestParty": {"name": "Example Party"},
            "latestHouseMembership": {
                "house": 1, "membershipFromId": constituency_id,
                "membershipStatus": {"statusIsActive": True},
            },
        },
    }
    return {"value": {
        "id": constituency_id, "name": name, "endDate": None,
        "currentRepresentation": None if member is None else {
            "member": member,
            "representation": {"membershipStartDate": "2024-07-04T00:00:00"},
        },
    }}


def _parliament_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    config_path, raw = _provider_fixture(tmp_path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    member_file = tmp_path / "source" / "parliament.json"
    member_file.write_text(json.dumps({"totalResults": 2, "items": [
        _member_item(100, "Aldershot", 123),
        _member_item(101, "Aldridge-Brownhills", None),
    ]}), encoding="utf-8")
    config["parliament_current_constituencies"] = {
        "url": member_file.as_uri(), "filename": member_file.name,
        "source_version": "UK Parliament Members API v1",
        "source_date": "retrieval", "publisher": "UK Parliament",
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path, raw, member_file


def _build(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    config, raw, member_file = _parliament_fixture(tmp_path)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    build_pipeline(config, raw, processed, offline=True)
    return config, raw, member_file, processed


def test_parliamentary_build_keeps_v01_v02_and_separates_signals(tmp_path: Path) -> None:
    _, _, _, processed = _build(tmp_path)
    assert (processed / ".gitkeep").read_text(encoding="utf-8") == "\n"
    members = pl.read_parquet(processed / "dim_pcon_member.parquet")
    assert members.height == 2
    assert members.filter(pl.col("pcon24cd") == "E14001064")["member_status"][0] == "vacant"
    links = pl.read_parquet(processed / "mp_nhs_relationship.parquet")
    practice = links.filter((pl.col("org_code") == "A81001")
                            & (pl.col("relationship_basis") == "registered_patients"))
    assert practice.select("pcon24cd", "registered_patients").sort("pcon24cd").rows() == [
        ("E14001063", 10), ("E14001064", 5),
    ]
    assert practice.filter(pl.col("pcon24cd") == "E14001064")["member_id"][0] is None
    assert links.filter(pl.col("relationship_basis") == "registered_patients")[
        "registered_patients"].sum() == 35
    assert links.filter(pl.col("relationship_basis") != "registered_patients").filter(
        pl.col("registered_patients").is_not_null()).height == 0
    assert links.filter((pl.col("org_code") == "T00001")
                        & (pl.col("relationship_basis") == "operating_relationship"))[
        "pcon24cd"][0] == "E14001064"
    profile = pl.read_parquet(processed / "organisation_parliamentary_profile.parquet")
    assert profile.height == 11
    trust = profile.filter(pl.col("org_code") == "R00001").row(0, named=True)
    assert trust["registered_patients_total"] is None
    assert trust["address_member_status"] == "vacant"
    assert profile.filter(pl.col("org_code") == "A81001")["registered_patients_total"][0] == 16
    brief = pl.read_parquet(processed / "pcon_parliamentary_brief.parquet")
    assert brief.height == 2
    assert brief["registered_patients_mapped"].sum() == 35
    assert pl.read_parquet(processed / "gp_practice_patient_pcon.parquet")["patient_count"].sum() == 36
    assert audit_outputs(processed)["parliamentary"]["patient_difference"] == 0
    assert (processed / "json" / "constituencies" / "E14001064.json").exists()
    assert (processed / "json" / "organisations" / "T00001.json").exists()


def test_cli_json_and_unknown_codes(tmp_path: Path) -> None:
    _, _, _, processed = _build(tmp_path)
    runner = CliRunner()
    for command, code, required in (
        ("constituency", "E14001064", "serving_gp_practices"),
        ("organisation", "A81001", "served_constituencies"),
    ):
        result = runner.invoke(app, [command, code, "--json", "--processed-dir", str(processed)])
        assert result.exit_code == 0, result.output
        assert required in json.loads(result.output)
        readable = runner.invoke(app, [command, code, "--processed-dir", str(processed)])
        assert readable.exit_code == 0, readable.output
        assert "registered_patients" in readable.output
        missing = runner.invoke(app, [command, "UNKNOWN", "--processed-dir", str(processed)])
        assert missing.exit_code != 0
        assert "Unknown" in missing.output


def test_cross_boundary_practice_keeps_both_current_mps(tmp_path: Path) -> None:
    config, raw, member_file = _parliament_fixture(tmp_path)
    payload = json.loads(member_file.read_text())
    payload["items"][1] = _member_item(101, "Aldridge-Brownhills", 456)
    member_file.write_text(json.dumps(payload))
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    build_pipeline(config, raw, processed, offline=True)
    practice = pl.read_parquet(processed / "mp_nhs_relationship.parquet").filter(
        (pl.col("org_code") == "A81001")
        & (pl.col("relationship_basis") == "registered_patients")
    ).sort("pcon24cd")
    assert practice["member_id"].to_list() == [123, 456]
    assert practice["registered_patients"].to_list() == [10, 5]
    assert practice["site_in_constituency"].to_list() == [True, False]
    assert practice["source_snapshot_date"].dt.strftime("%Y-%m-%d").to_list() == [
        "2026-07-01", "2026-07-01"]
    assert practice["member_source_snapshot_date"].n_unique() == 1


def test_cli_refreshes_member_without_changing_other_raw_sources(tmp_path: Path) -> None:
    config, raw, member_file, processed = _build(tmp_path)
    before = json.loads((raw / "sources_manifest.json").read_text())["sources"]
    payload = json.loads(member_file.read_text())
    payload["items"][1] = _member_item(101, "Aldridge-Brownhills", 456)
    member_file.write_text(json.dumps(payload))
    result = CliRunner().invoke(app, ["fetch", "--refresh-member", "--config", str(config),
                                      "--raw-dir", str(raw)])
    assert result.exit_code == 0, result.output
    after = json.loads((raw / "sources_manifest.json").read_text())["sources"]
    assert before["ods_gp_practices"] == after["ods_gp_practices"]
    assert before["gp_registered_patients_lsoa"] == after["gp_registered_patients_lsoa"]
    assert before["parliament_current_constituencies"]["sha256"] != after[
        "parliament_current_constituencies"]["sha256"]
    build_pipeline(config, raw, processed, offline=True)
    assert pl.read_parquet(processed / "dim_pcon_member.parquet").filter(
        pl.col("pcon24cd") == "E14001064")["member_id"][0] == 456


def test_member_mismatch_duplicate_and_vacancy_validation(tmp_path: Path) -> None:
    config, raw, member_file, processed = _build(tmp_path)
    pcon = pl.read_parquet(processed / "pcon24.parquet")
    ledger = json.loads((raw / "sources_manifest.json").read_text())["sources"][
        "parliament_current_constituencies"]
    payload = json.loads(member_file.read_text())
    payload["items"][1]["value"]["name"] = "Unknown constituency"
    member_file.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="name mismatch"):
        read_members(member_file, pcon, ledger)
    payload["items"][1]["value"]["name"] = "Aldershot"
    member_file.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Duplicate Parliament constituency"):
        read_members(member_file, pcon, ledger)
    payload["items"][1] = _member_item(101, "Aldridge-Brownhills", 123)
    member_file.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="multiple constituencies"):
        read_members(member_file, pcon, ledger)
    assert load_config(config)


def test_official_welsh_accent_name_bridge(tmp_path: Path) -> None:
    source = tmp_path / "members.json"
    source.write_text(json.dumps({"totalResults": 1, "items": [
        _member_item(100, "Montgomeryshire and Glyndŵr", None),
    ]}), encoding="utf-8")
    pcon = pl.DataFrame({"pcon24cd": ["W07000102"],
                         "pcon24nm": ["Montgomeryshire and Glyndwr"]})
    result = read_members(source, pcon, {
        "source_date": "2026-10-10", "source_version": "API v1", "url": "https://example.test",
        "retrieved_at": "2026-10-10T00:00:00+00:00",
    })
    assert result["pcon24cd"].to_list() == ["W07000102"]
    assert result["member_status"].to_list() == ["vacant"]


def test_failed_member_rebuild_preserves_complete_previous_bundle(tmp_path: Path) -> None:
    config, raw, member_file, processed = _build(tmp_path)
    files = {path.name: sha256_file(path) for path in processed.iterdir() if path.is_file()}
    json_file = processed / "json" / "constituencies" / "E14001064.json"
    previous_json = sha256_file(json_file)
    payload = json.loads(member_file.read_text())
    payload["items"][1]["value"]["name"] = "Wrong name"
    member_file.write_text(json.dumps(payload))
    fetch_sources({"parliament_current_constituencies": load_config(config)[
        "parliament_current_constituencies"]}, raw, refresh=True)
    with pytest.raises(ValueError, match="name mismatch"):
        build_pipeline(config, raw, processed, offline=True)
    assert {path.name: sha256_file(path) for path in processed.iterdir() if path.is_file()} == files
    assert sha256_file(json_file) == previous_json


def test_parliamentary_outputs_deterministic(tmp_path: Path) -> None:
    config, raw, _, processed = _build(tmp_path)
    paths = [processed / f"{name}.parquet" for name in (
        "dim_pcon_member", "pcon_parliamentary_brief", "organisation_parliamentary_profile",
        "mp_nhs_relationship")]
    paths += [processed / "json" / "constituencies" / "E14001064.json",
              processed / "json" / "organisations" / "A81001.json"]
    before = [sha256_file(path) for path in paths]
    build_pipeline(config, raw, processed, offline=True)
    assert [sha256_file(path) for path in paths] == before


def test_saved_output_qa_catches_profile_patient_loss(tmp_path: Path) -> None:
    _, _, _, processed = _build(tmp_path)
    path = processed / "organisation_parliamentary_profile.parquet"
    profile = pl.read_parquet(path).with_columns(
        pl.when(pl.col("org_code") == "A81001").then(pl.lit("[]"))
        .otherwise(pl.col("served_constituencies_json")).alias("served_constituencies_json")
    )
    profile.write_parquet(path)
    with pytest.raises(ValueError, match="GP parliamentary profile does not reconcile"):
        audit_outputs(processed)
