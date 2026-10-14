"""Integrity, access control and rollback regressions on fictional assets."""

import base64
import io
import json
import tarfile
import threading
from pathlib import Path

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
            assert redirect.headers["location"] == "/index.html?view=organisation&code=XG00001"
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
