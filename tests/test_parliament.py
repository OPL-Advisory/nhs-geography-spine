"""Offline parliamentary member, relationship, CLI and publication regressions."""

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
import yaml
from test_pipeline import _csv, _fixture_sources
from test_providers import _provider_fixture, _report
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


@pytest.mark.parametrize("kind,code", [
    ("constituencies", "E14001063"),
    ("organisations", "A81001"),
])
def test_saved_output_qa_rejects_malformed_json(tmp_path: Path, kind: str, code: str) -> None:
    _, _, _, processed = _build(tmp_path)
    path = processed / "json" / kind / f"{code}.json"
    path.write_text('{"truncated":', encoding="utf-8")
    with pytest.raises(ValueError, match=rf"Invalid parliamentary JSON at .*{code}\.json"):
        audit_outputs(processed)


@pytest.mark.parametrize("kind", ["constituencies", "organisations"])
def test_saved_output_qa_rejects_swapped_json(tmp_path: Path, kind: str) -> None:
    _, _, _, processed = _build(tmp_path)
    first, second = sorted((processed / "json" / kind).glob("*.json"))[:2]
    first.write_bytes(second.read_bytes())
    with pytest.raises(ValueError, match=rf"Parliamentary JSON identifier mismatch .*{first.name}"):
        audit_outputs(processed)


@pytest.mark.parametrize("kind,code,field,replacement", [
    ("constituencies", "E14001063", "member_name", "Stale member"),
    ("constituencies", "E14001063", "registered_patients_mapped", -1),
    ("organisations", "A81001", "organisation_type", "nhs_trust"),
    ("organisations", "A81001", "registered_patients_total", -1),
    ("organisations", "T00001", "parent_relationship_basis", None),
])
def test_saved_output_qa_rejects_stale_json_fields(
    tmp_path: Path, kind: str, code: str, field: str, replacement: object,
) -> None:
    _, _, _, processed = _build(tmp_path)
    path = processed / "json" / kind / f"{code}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[field] != replacement
    payload[field] = replacement
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=rf"Parliamentary JSON mismatch .*{field} differs"):
        audit_outputs(processed)


@pytest.mark.parametrize("kind,code,array,change", [
    ("constituencies", "E14001063", "site_organisations", "missing"),
    ("constituencies", "E14001063", "serving_gp_practices", "patient_share"),
    ("constituencies", "E14001064", "operating_relationships", "re6_status"),
    ("organisations", "A81001", "relationships", "extra"),
    ("organisations", "T00001", "relationships", "re6_status"),
])
def test_saved_output_qa_rejects_stale_json_relationships(
    tmp_path: Path, kind: str, code: str, array: str, change: str,
) -> None:
    _, _, _, processed = _build(tmp_path)
    path = processed / "json" / kind / f"{code}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[array]
    if change == "missing":
        payload[array].pop()
    elif change == "patient_share":
        payload[array][0]["share_of_practice_list"] = -1.0
    elif change == "re6_status":
        relationship = next(row for row in payload[array]
                            if row["relationship_basis"] == "operating_relationship")
        relationship["relationship_temporal_status"] = "expired"
    else:
        payload[array].append(payload[array][0])
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=rf"Parliamentary JSON mismatch .*{array} differs"):
        audit_outputs(processed)


def test_re6_dates_gate_current_parliamentary_evidence(tmp_path: Path) -> None:
    config, raw, _ = _parliament_fixture(tmp_path)
    branches = Path(load_config(config)["ods_gp_branches"].url.removeprefix("file://"))
    snapshot = datetime.now(UTC).date()
    expired = _report("B00003", "Expired RE6 branch", "SW1A 2AA", parent="A81001")
    expired[15] = (snapshot - timedelta(days=10)).strftime("%Y%m%d")
    expired[16] = (snapshot - timedelta(days=1)).strftime("%Y%m%d")
    future = _report("B00004", "Future RE6 branch", "SW1A 2AA", parent="A81001")
    future[15] = (snapshot + timedelta(days=1)).strftime("%Y%m%d")
    boundary = _report("B00005", "Current RE6 branch", "SW1A 2AA", parent="A81001")
    boundary[15] = snapshot.strftime("%Y%m%d")
    boundary[16] = snapshot.strftime("%Y%m%d")
    branches.write_bytes(branches.read_bytes() + _csv([expired, future, boundary]))
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    report = build_pipeline(config, raw, processed, offline=True)
    assert report["parliamentary"]["active_re6_by_temporal_status"] == {
        "effective": 6, "expired": 1, "future": 1,
    }
    links = pl.read_parquet(processed / "mp_nhs_relationship.parquet")
    operators = links.filter(pl.col("relationship_basis") == "operating_relationship")
    assert "B00003" not in operators["org_code"].to_list()
    assert "B00004" not in operators["org_code"].to_list()
    current = operators.filter(pl.col("org_code") == "B00005").row(0, named=True)
    assert current["relationship_start_date"] == snapshot
    assert current["relationship_end_date"] == snapshot
    assert current["relationship_temporal_status"] == "current"
    profile = {row["org_code"]: row for row in pl.read_parquet(
        processed / "organisation_parliamentary_profile.parquet").to_dicts()}
    for code, status, start, end in (
        ("B00003", "expired", snapshot - timedelta(days=10), snapshot - timedelta(days=1)),
        ("B00004", "future", snapshot + timedelta(days=1), None),
    ):
        row = profile[code]
        assert row["parent_relationship_temporal_status"] == status
        assert row["parent_relationship_current_at_snapshot"] is False
        assert row["parent_relationship_basis"] is None
        assert row["parent_address_member_name"] is None
        assert row["parent_current_address_pcon24cd"] is None
        assert row["parent_address_pcon24cd"] == "E14001063"  # retained source context
        assert row["relationship_start_date"] == start
        assert row["relationship_end_date"] == end
        detail = json.loads((processed / "json" / "organisations" / f"{code}.json").read_text())
        assert detail["parent_relationship_temporal_status"] == status
        assert detail["relationship_end_date"] == (str(end) if end else None)
        readable = CliRunner().invoke(app, ["organisation", code, "--processed-dir", str(processed)])
        assert readable.exit_code == 0, readable.output
        assert f"{status}; not a current parliamentary link" in readable.output
    assert profile["B00005"]["parent_relationship_temporal_status"] == "effective"
    assert profile["B00005"]["parent_relationship_current_at_snapshot"] is True
    assert profile["B00005"]["parent_relationship_basis"] == "operating_relationship"
    assert profile["B00005"]["parent_address_member_name"] == "Member 123"
    pcon_json = json.loads((processed / "json" / "constituencies" / "E14001063.json").read_text())
    codes = {row["org_code"] for row in pcon_json["operating_relationships"]}
    assert "B00005" in codes and "B00003" not in codes and "B00004" not in codes
    assert audit_outputs(processed)["parliamentary"]["active_re6_by_temporal_status"] == (
        report["parliamentary"]["active_re6_by_temporal_status"])


def test_custom_processed_dir_preserves_clean_prebuild_git_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    config, raw = _fixture_sources(inputs)
    fetch_sources(load_config(config), raw)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "README.md").write_text("clean checkout\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "Initial commit"],
                   cwd=repo, check=True)
    expected_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                           text=True).strip()
    monkeypatch.chdir(repo)
    processed = repo / "outputs"  # deliberately outside the project's default ignore rule
    build_pipeline(config, raw, processed, offline=True)
    manifest = json.loads((processed / "build_manifest.json").read_text())
    assert manifest["code"]["git_sha"] == expected_sha
    assert manifest["code"]["git_dirty"] is False
    assert "?? outputs/" in subprocess.check_output(["git", "status", "--porcelain"],
                                                      cwd=repo, text=True)
