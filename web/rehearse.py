"""Run the reproducible synthetic capacity/upload/rollback/browser rehearsal locally."""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path

import httpx

from web import build, release, synthetic


def rehearse(directory: Path, *, full_size: bool = False, browser: bool = True) -> dict:
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Use a new empty rehearsal directory; existing evidence is preserved")
    directory.mkdir(parents=True, exist_ok=True)
    counts = {"organisation_count": 64_040, "constituency_count": 650, "padding": 2304} \
        if full_size else {"organisation_count": 40, "constituency_count": 4, "padding": 0}
    report = {"scope": "Loopback synthetic rehearsal; no provider upload or hosting mutation",
              "full_size": full_size, "candidates": [], "checks": {}, "timings_seconds": {}}
    candidates = []
    for variant in (1, 2):
        start = time.monotonic()
        site = directory / f"site-{variant}"
        evidence = synthetic.generate(directory / f"inputs-{variant}", site, variant=variant, **counts)
        archive = directory / f"candidate-{variant}.tar.gz"
        sha = build.make_archive(site, archive)
        evidence.update(archive_sha256=sha, archive_bytes=archive.stat().st_size)
        report["candidates"].append(evidence)
        candidates.append((archive, sha))
        report["timings_seconds"][f"generate_archive_{variant}"] = round(time.monotonic() - start, 2)
    repeat = directory / "candidate-repeat.tar.gz"
    repeat_sha = build.make_archive(directory / "site-1", repeat)
    if repeat_sha != candidates[0][1]:
        raise ValueError("Non-reproducible candidate archive")
    report["checks"]["repeat_archive_identical"] = True
    store = directory / "store"
    for archive, sha in candidates:
        start = time.monotonic()
        release.install(archive, sha, store)
        report["timings_seconds"][f"local_extract_verify_{sha[:12]}"] = round(time.monotonic() - start, 2)
    release.pin(store, candidates[0][1])
    user = "local-rehearsal"
    password = secrets.token_urlsafe(24)
    server = release.preview_server(store, user, password)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(base_url=url, auth=(user, password), follow_redirects=True) as client:
            initial = client.get("/release.json").json()
            route = build.detail_relative("organisations", synthetic.GP, initial["detail_layout"])
            old_path = f"/snapshots/{initial['snapshot_id']}/{route}"
            old = client.get(old_path)
            old.raise_for_status()
            report["checks"]["unauthorised_all_asset_classes"] = all(
                httpx.get(url + path).status_code == 401 for path in
                ("/", "/release.json", "/app.js", old_path, "/snapshots/absent.json"))
            assert report["checks"]["unauthorised_all_asset_classes"]
            report["checks"]["cache_and_404"] = (
                client.get("/release.json").headers["cache-control"] == "no-cache"
                and old.headers["cache-control"] == "private, max-age=31536000, immutable"
                and client.get("/missing.json").status_code == 404)
            assert report["checks"]["cache_and_404"]
            report["checks"]["atomic_pin_switch"] = release.pin(store, candidates[1][1])[
                "snapshot_id"] == report["candidates"][1]["snapshot_id"]
            assert client.get("/release.json").json()["snapshot_id"] == report["candidates"][1]["snapshot_id"]
            assert client.get(old_path).content == old.content
            release.pin(store, candidates[0][1])
            assert client.get("/release.json").json() == initial
            assert client.get(old_path).content == old.content
            report["checks"]["rollback_same_deep_link_and_bytes"] = True
            report["checks"]["previous_session_assets_retained"] = True
        if browser:
            environment = {**os.environ, "LENS_SITE_URL": url, "LENS_PREVIEW_USER": user,
                           "LENS_PREVIEW_PASSWORD": password,
                           "LENS_EVIDENCE_DIR": str((directory / "browser").resolve())}
            start = time.monotonic()
            subprocess.run(["node", "web/tests/browser-check.mjs"], check=True, env=environment)
            report["timings_seconds"]["browser_checks"] = round(time.monotonic() - start, 2)
            report["checks"]["browser"] = True
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        (directory / "hosting-results.json").write_bytes(build._encoded(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--full-size", action="store_true")
    parser.add_argument("--skip-browser", action="store_true")
    args = parser.parse_args()
    result = rehearse(args.directory, full_size=args.full_size, browser=not args.skip_browser)
    print(build._encoded(result).decode(), end="")


if __name__ == "__main__":
    main()
