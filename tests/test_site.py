"""Offline checks for the read-only v0.3 JSON consumer."""

import hashlib
import json
from pathlib import Path

import pytest
from test_parliament import _build

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


def test_site_build_is_deterministic_and_archive_rolls_back(tmp_path: Path) -> None:
    processed, destination, first = _candidate(tmp_path)
    hashes = (destination / first["asset_hashes_path"]).read_bytes()
    assert hashlib.sha256(hashes).hexdigest() == first["snapshot_id"]
    for name in site.STATIC_FILES:
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == first[
            "static_asset_sha256"][name]
    archive1 = tmp_path / "candidate-1.tar.gz"
    archive2 = tmp_path / "candidate-2.tar.gz"
    digest1 = site.make_archive(destination, archive1)
    second = site.build_site(processed, destination)
    digest2 = site.make_archive(destination, archive2)
    assert second == first
    assert digest1 == digest2
    assert archive1.read_bytes() == archive2.read_bytes()
    with pytest.raises(ValueError, match="outside"):
        site.make_archive(destination, destination / "inside.tar.gz")


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
