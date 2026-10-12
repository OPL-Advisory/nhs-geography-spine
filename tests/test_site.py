"""Offline checks for the read-only v0.3 JSON consumer."""

import hashlib
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest
from test_parliament import _build, _parliament_fixture
from test_pipeline import _csv

from nhs_geo_spine.build import build_pipeline
from nhs_geo_spine.sources import fetch_sources, load_config
from web import build as site


def _candidate(tmp_path: Path) -> tuple[Path, Path, dict]:
    _, _, _, processed = _build(tmp_path)
    destination = tmp_path / "site"
    release = site.build_site(processed, destination)
    return processed, destination, release


def _detail(destination: Path, release: dict, kind: str, code: str) -> dict:
    folder = "constituencies" if kind == "constituency" else "organisations"
    return json.loads((destination / release["details_base"] / folder /
                       f"{code}.json").read_text(encoding="utf-8"))


def test_site_routes_dates_vacancy_and_separate_evidence(tmp_path: Path) -> None:
    _, destination, release = _candidate(tmp_path)
    assert release["counts"] == {"constituencies": 2, "organisations": 11}
    assert release["dates"]["parliament"] != release["dates"]["gp_patients"]
    for kind, folder in (("constituency", "constituencies"),
                         ("organisation", "organisations")):
        index = json.loads((destination / release["indexes"][folder]["path"])
                           .read_text(encoding="utf-8"))
        for code, name, *_ in index:
            detail = _detail(destination, release, kind, code)
            assert (detail["code"], detail["name"], detail["kind"]) == (code, name, kind)
    vacant = _detail(destination, release, "constituency", "E14001064")
    assert vacant["member"]["status"] == "vacant"
    assert vacant["member"]["name"] is None
    assert {row["basis"] for row in vacant["sites"]} == {"site_location"}
    assert {row["basis"] for row in vacant["practices"]} == {"registered_patients"}
    assert {row["basis"] for row in vacant["operators"]} <= {"operating_relationship"}
    cross_boundary = next(row for row in vacant["practices"] if row["org_code"] == "A81001")
    assert cross_boundary["registered_patients"] == 5
    assert cross_boundary["address_pcon24cd"] != vacant["code"]
    practice = _detail(destination, release, "organisation", "A81001")
    assert [(row["code"], row["registered_patients"]) for row in
            practice["served_constituencies"]] == [("E14001063", 10), ("E14001064", 5)]
    assert practice["unmapped_patient_count"] == 1
    trust = _detail(destination, release, "organisation", "R00001")
    assert trust["served_constituencies"] == []
    assert trust["address"]["member_status"] == "vacant"


def test_patient_only_practice_and_unresolved_re6_have_no_missing_profile_links(
    tmp_path: Path,
) -> None:
    config, raw, _ = _parliament_fixture(tmp_path)
    patients = Path(load_config(config)["gp_registered_patients_lsoa"].url.removeprefix("file://"))
    member = "gp-reg-pat-prac-lsoa-all.csv"
    with zipfile.ZipFile(patients) as archive:
        original = archive.read(member)
    extra = _csv([["GP_PRAC_PAT_LIST", "2026-07-01", "A81999", "Patient-only practice",
                   "E01000001", "ALL", "7"]])
    with zipfile.ZipFile(patients, "w") as archive:
        archive.writestr(member, original + extra)
    fetch_sources(load_config(config), raw)
    processed = tmp_path / "processed"
    build_pipeline(config, raw, processed, offline=True)
    destination = tmp_path / "site"
    release = site.build_site(processed, destination)

    constituency = _detail(destination, release, "constituency", "E14001063")
    patient_only = next(row for row in constituency["practices"]
                        if row["org_code"] == "A81999")
    assert patient_only["registered_patients"] == 7
    assert patient_only["profile_available"] is False
    assert not (destination / release["details_base"] / "organisations" / "A81999.json").exists()
    assert next(row for row in constituency["practices"] if row["org_code"] == "A81001")[
        "profile_available"] is True

    unresolved = _detail(destination, release, "organisation", "B00002")
    assert unresolved["operator"]["code"] == "Y00001"
    assert unresolved["operator"]["profile_available"] is False
    assert not (destination / release["details_base"] / "organisations" / "Y00001.json").exists()
    resolved = _detail(destination, release, "organisation", "B00001")
    assert resolved["operator"]["profile_available"] is True
    for code, *_ in json.loads((destination / release["indexes"]["constituencies"]["path"])
                               .read_text(encoding="utf-8")):
        detail = _detail(destination, release, "constituency", code)
        for row in detail["practices"]:
            exists = (destination / release["details_base"] / "organisations" /
                      f"{row['org_code']}.json").exists()
            assert row["profile_available"] is exists


def test_site_build_is_deterministic_and_archive_rolls_back(tmp_path: Path) -> None:
    processed, destination, first = _candidate(tmp_path)
    hashes = (destination / first["asset_hashes_path"]).read_bytes()
    assert hashlib.sha256(hashes).hexdigest() == first["snapshot_id"]
    for name in site.STATIC_FILES:
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == first[
            "static_asset_sha256"][name]
    archive1 = tmp_path / "candidate-1.tar.gz"
    archive2 = tmp_path / "candidate-2.tar.gz"
    detail = destination / first["details_base"] / "organisations" / "A81001.json"
    original_umask = os.umask(0o022)
    try:
        assert site.build_site(processed, destination) == first
        mode_22 = detail.stat().st_mode & 0o777
        digest1 = site.make_archive(destination, archive1)
        os.umask(0o077)
        second = site.build_site(processed, destination)
        mode_77 = detail.stat().st_mode & 0o777
        digest2 = site.make_archive(destination, archive2)
    finally:
        os.umask(original_umask)
    assert second == first
    assert (mode_22, mode_77) == (0o644, 0o600)
    assert digest1 == digest2
    assert archive1.read_bytes() == archive2.read_bytes()
    with tarfile.open(archive2, "r:gz") as bundle:
        assert all(member.mode == (0o644 if member.isfile() else 0o755)
                   for member in bundle.getmembers())
    with pytest.raises(ValueError, match="outside"):
        site.make_archive(destination, destination / "inside.tar.gz")


@pytest.mark.parametrize('failure', ['symlink', 'read', 'hash', 'replace'])
def test_failed_archive_replacement_preserves_previous_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    _, destination, _ = _candidate(tmp_path)
    archive = tmp_path / 'rollback.tar.gz'
    previous_digest = site.make_archive(destination, archive)
    previous_bytes = archive.read_bytes()
    original_open = Path.open
    original_replace = Path.replace

    if failure == 'symlink':
        (destination / '.000-invalid-link').symlink_to(destination / 'release.json')
    elif failure == 'read':
        def fail_read(self: Path, mode: str = 'r', *args, **kwargs):
            if self == destination / 'release.json' and mode == 'rb':
                raise OSError('simulated source read failure')
            return original_open(self, mode, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', fail_read)
    elif failure == 'hash':
        def fail_hash(_content: bytes) -> str:
            raise OSError('simulated archive hash failure')
        monkeypatch.setattr(site, '_hash', fail_hash)
    else:
        def fail_replace(self: Path, target: Path) -> Path:
            if target == archive:
                raise OSError('simulated archive replace failure')
            return original_replace(self, target)
        monkeypatch.setattr(Path, 'replace', fail_replace)

    with pytest.raises((ValueError, OSError), match='Symlink|simulated'):
        site.make_archive(destination, archive)
    assert archive.read_bytes() == previous_bytes
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == previous_digest
    assert not list(tmp_path.glob('.rollback.tar.gz.*'))


def test_site_refuses_unowned_nested_file_before_writing(tmp_path: Path) -> None:
    processed, destination, release = _candidate(tmp_path)
    nested = destination / "snapshots" / release["snapshot_id"] / "details" / "organisations" / "notes.txt"
    nested.write_text("preserve this unrelated file\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unowned file"):
        site.build_site(processed, destination)
    assert nested.read_text(encoding="utf-8") == "preserve this unrelated file\n"
    assert (destination / "release.json").exists()


def test_failed_projection_preserves_previous_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    processed, destination, release = _candidate(tmp_path)
    old_bytes = (destination / "release.json").read_bytes()

    def fail(_source: dict) -> dict:
        raise ValueError("projection failed")

    monkeypatch.setattr(site, "_organisation", fail)
    with pytest.raises(ValueError, match="projection failed"):
        site.build_site(processed, destination)
    assert (destination / "release.json").read_bytes() == old_bytes
    assert (destination / "snapshots" / release["snapshot_id"]).is_dir()
    assert not list(tmp_path.glob(".site-build-*"))


def test_failed_atomic_publish_restores_previous_release(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    processed, destination, release = _candidate(tmp_path)
    old_bytes = (destination / "release.json").read_bytes()
    original = Path.replace

    def fail_new_stage(self: Path, target: Path) -> Path:
        if self.name.startswith(".site-build-") and target == destination:
            raise OSError("simulated final rename failure")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail_new_stage)
    with pytest.raises(OSError, match="simulated final rename failure"):
        site.build_site(processed, destination)
    assert (destination / "release.json").read_bytes() == old_bytes
    assert (destination / "snapshots" / release["snapshot_id"]).is_dir()
    assert not (tmp_path / ".site-previous").exists()


def test_static_shell_has_keyboard_and_responsive_basics() -> None:
    html = (site.HERE / "index.html").read_text(encoding="utf-8")
    app = (site.HERE / "app.js").read_text(encoding="utf-8")
    css = (site.HERE / "styles.css").read_text(encoding="utf-8")
    assert 'class="skip-link"' in html
    assert 'role="combobox"' in html and 'role="listbox"' in html
    assert 'aria-live="polite"' in html
    assert all(key in app for key in ("ArrowDown", "ArrowUp", "Enter", "Escape", "Tab"))
    assert "@media (max-width: 540px)" in css
    assert "prefers-reduced-motion" in css
