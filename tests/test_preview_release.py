"""Integrity, access control and rollback regressions on fictional assets."""

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
from pathlib import Path
from urllib.parse import urljoin

import httpx
import pytest

from web import build, release, synthetic


def _fixture(tmp_path: Path, variant: int = 1) -> tuple[Path, str]:
    site = tmp_path / f"site-{variant}"
    synthetic.generate(tmp_path / f"inputs-{variant}", site,
                       organisation_count=9, constituency_count=4, padding=0, variant=variant)
    archive = tmp_path / f"candidate-{variant}.tar.gz"
    return archive, build.make_archive(site, archive)


def test_reproducible_synthetic_archive_and_all_relationship_targets(tmp_path: Path) -> None:
    archive, sha = _fixture(tmp_path)
    second = tmp_path / "repeat.tar.gz"
    assert build.make_archive(tmp_path / "site-1", second) == sha
    evidence = release.verify_site(tmp_path / "site-1")
    assert evidence["synthetic"] is True
    assert evidence["counts"] == {"constituencies": 4, "organisations": 9}
    assert evidence["verified_relationship_targets"] is True
    assert archive.read_bytes() == second.read_bytes()


def test_unowned_or_symlink_store_preserves_unrelated_files(tmp_path: Path) -> None:
    archive, sha = _fixture(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    note = unrelated / "keep.txt"
    note.write_text("preserve")
    with pytest.raises(ValueError, match="Unowned release store"):
        release.install(archive, sha, unrelated)
    assert note.read_text() == "preserve"
    store = tmp_path / "store"
    store.mkdir()
    (store / release.STORE_MARKER).write_text("nhs-lens-loopback-store-v1\n")
    (store / "candidates").symlink_to(unrelated, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink inside release store"):
        release.install(archive, sha, store)
    assert list(unrelated.iterdir()) == [note]


def test_install_hash_and_tampered_asset_cannot_replace_good_pin(tmp_path: Path) -> None:
    archive, sha = _fixture(tmp_path)
    store = tmp_path / "store"
    release.install(archive, sha, store)
    release.pin(store, sha)
    previous = (store / "current.json").read_bytes()
    with pytest.raises(ValueError, match="Archive SHA"):
        release.install(archive, "a" * 64, store)
    asset = store / "candidates" / sha / "app.js"
    asset.write_text("tampered")
    with pytest.raises(ValueError, match="Asset integrity"):
        release.pin(store, sha)
    assert (store / "current.json").read_bytes() == previous
    assert not list(store.glob(".pin-*"))


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/escape", "file"),
                                      ("link", "symlink"), ("duplicate", "duplicate")])
def test_unsafe_archive_never_becomes_a_candidate(tmp_path: Path, name: str, kind: str) -> None:
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo(name)
        if kind == "symlink":
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            bundle.addfile(info)
        else:
            info.size = 1
            bundle.addfile(info, io.BytesIO(b"x"))
            if kind == "duplicate":
                bundle.addfile(info, io.BytesIO(b"x"))
    store = tmp_path / "store"
    with pytest.raises(ValueError, match="Unsafe archive"):
        release.install(archive, release.digest(archive), store)
    assert not list((store / "candidates").iterdir())
    assert not list(store.glob(".install-*"))


def test_atomic_pin_failure_preserves_previous_and_retains_both_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "store"
    first, sha1 = _fixture(tmp_path, 1)
    second, sha2 = _fixture(tmp_path, 2)
    release.install(first, sha1, store)
    release.install(second, sha2, store)
    release.pin(store, sha1)
    previous = (store / "current.json").read_bytes()
    original = Path.replace
    def fail(self, target):
        if self.name.startswith(".pin-"):
            raise OSError("simulated pin failure")
        return original(self, target)
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="simulated"):
        release.pin(store, sha2)
    assert (store / "current.json").read_bytes() == previous
    assert len(list((store / "candidates").iterdir())) == 2
    assert not list(store.glob(".pin-*"))


def test_protected_host_cache_404_and_query_links_survive_upgrade_and_rollback(tmp_path: Path) -> None:
    store = tmp_path / "store"
    first, sha1 = _fixture(tmp_path, 1)
    second, sha2 = _fixture(tmp_path, 2)
    release.install(first, sha1, store)
    release.install(second, sha2, store)
    release.pin(store, sha1)
    snapshot1 = json.loads((tmp_path / "site-1" / "release.json").read_bytes())
    old_detail = "snapshots/" + snapshot1["snapshot_id"] + "/details/organisations/XG/XG00001.json"
    server = release.preview_server(store, "tester", "local-test-secret")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    auth = "Basic " + base64.b64encode(b"tester:local-test-secret").decode()
    try:
        with httpx.Client(base_url=url, follow_redirects=False) as client:
            for path in ("/", "/index.html", "/release.json", "/app.js", "/" + old_detail,
                         "/no-such-file", "/snapshots/missing/details.json"):
                blocked = client.get(path)
                assert blocked.status_code == 401
                assert blocked.headers["cache-control"] == "no-store"
                assert client.head(path).status_code == 401
            client.headers["Authorization"] = auth
            redirect = client.get("/?view=organisation&code=XG00001")
            assert redirect.status_code == 302
            assert redirect.headers["location"] == f"/candidates/{sha1}/index.html?view=organisation&code=XG00001"
            assert redirect.headers["cache-control"] == "no-store"
            client.follow_redirects = True
            assert client.get("/release.json").headers["cache-control"] == "no-cache"
            before = client.get("/" + old_detail)
            assert before.headers["cache-control"] == "private, max-age=31536000, immutable"
            assert before.headers["content-encoding"] == "gzip"
            assert "Authorization" in before.headers["vary"]
            assert client.get("/no-such-file").status_code == 404
            assert client.get("/%2E%2E/current.json").status_code == 404
            assert client.get("/.nhs-parliamentary-lens-output").status_code == 404
            release.pin(store, sha2)
            assert client.get("/" + old_detail).json() == before.json()
            assert client.get("/release.json").json()["snapshot_id"] != snapshot1["snapshot_id"]
            release.pin(store, sha1)
            assert client.get("/release.json").json() == snapshot1
            assert client.get("/" + old_detail).json() == before.json()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_initial_page_load_stays_on_one_candidate_during_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A small schema-1 fixture preserves the actual v0.4 flat-fetch contract.
    shell = tmp_path / "legacy-shell"
    shell.mkdir()
    for name in build.STATIC_FILES:
        shutil.copyfile(build.HERE / name, shell / name)
    shutil.copyfile(build.HERE / "tests" / "fixtures" / "flat-v1-app.mjs", shell / "app.js")
    with monkeypatch.context() as old:
        old.setattr(build, "HERE", shell)
        old.setattr(build, "DETAIL_LAYOUT", {"constituencies": "flat", "organisations": "flat"})
        first, _ = _fixture(tmp_path, 1)
    release_file = tmp_path / "site-1" / "release.json"
    metadata = json.loads(release_file.read_bytes())
    metadata["schema_version"] = 1
    metadata.pop("detail_layout")
    release_file.write_bytes(build._encoded(metadata))
    sha1 = build.make_archive(tmp_path / "site-1", first)
    second, sha2 = _fixture(tmp_path, 2)
    store = tmp_path / "store"
    release.install(first, sha1, store)
    release.install(second, sha2, store)
    release.pin(store, sha1)
    server = release.preview_server(store, "tester", "local-test-secret")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(base_url=url, auth=("tester", "local-test-secret"), follow_redirects=True) as client:
            entry = client.get("/index.html?view=organisation&code=XG00001")
            assert entry.status_code == 200
            assert entry.url.path == f"/candidates/{sha1}/index.html"
            app = client.get(urljoin(str(entry.url), "app.js"))
            assert app.status_code == 200
            release.pin(store, sha2)  # Script has arrived; its metadata has not.
            old_metadata = client.get(urljoin(str(app.url), "release.json")).json()
            assert old_metadata["schema_version"] == 1
            detail = client.get(urljoin(str(app.url),
                old_metadata["details_base"] + "/organisations/XG00001.json"))
            assert detail.status_code == 200 and detail.json()["code"] == "XG00001"
            assert client.get("/release.json").json()["schema_version"] == 2
            assert client.get(f"/candidates/{sha2}/" + old_metadata["details_base"] +
                              "/organisations/XG00001.json").status_code == 404
            release.pin(store, sha1)
            assert client.get(f"/candidates/{sha2}/release.json").json()["schema_version"] == 2
            assert client.get("/candidates/invalid/index.html").status_code == 404
            for path in (str(entry.url), f"{url}/candidates/{sha1}/release.json",
                         f"{url}/candidates/{sha1}/app.js"):
                assert httpx.get(path).status_code == 401
        if os.environ.get("LENS_BROWSER_CHECKS") == "1":
            subprocess.run(["node", "web/tests/pin-during-load.mjs"], check=True, env={
                **os.environ, "LENS_SITE_URL": url, "LENS_PREVIEW_USER": "tester",
                "LENS_PREVIEW_PASSWORD": "local-test-secret", "LENS_PYTHON": sys.executable,
                "LENS_TEST_STORE": str(store.resolve()), "LENS_LEGACY_SHA": sha1, "LENS_NEW_SHA": sha2,
                "LENS_PIN_REPORT": os.environ.get("LENS_PIN_REPORT", str(tmp_path / "browser-pin.json")),
            })
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
