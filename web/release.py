"""Verify/install/pin an artifact and rehearse protected hosting on loopback only.

No provider API, DNS, release upload or external listener is supported here.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import tarfile
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp
from urllib.parse import unquote, urlsplit

from web import build

SHA = re.compile(r"^[a-f0-9]{64}$")
STORE_MARKER = ".nhs-lens-release-store"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_site(root: Path) -> dict:
    """Verify every deployed byte, index target and rendered relationship target."""
    build._check_destination(root, root.parent / "__not_processed_inputs")
    release = json.loads((root / "release.json").read_bytes())
    snapshot = root / "snapshots" / release["snapshot_id"]
    if release.get("schema_version") not in {1, 2} \
            or release["details_base"] != f"snapshots/{release['snapshot_id']}/details" \
            or release["asset_hashes_path"] != f"snapshots/{release['snapshot_id']}/asset-hashes.json":
        raise ValueError("Invalid release layout/path contract")
    manifest_path = snapshot / "asset-hashes.json"
    manifest_sha = digest(manifest_path)
    if manifest_sha != release["snapshot_id"] or manifest_sha != release["asset_hashes_sha256"]:
        raise ValueError("Snapshot manifest hash mismatch")
    hashes = json.loads(manifest_path.read_bytes())
    if hashes["static_asset_sha256"] != release["static_asset_sha256"]:
        raise ValueError("Static manifest differs from release")
    if hashes["source_manifest_sha256"] != release["source_manifest_sha256"]:
        raise ValueError("Source manifest differs from release")
    for base, entries in ((root, hashes["static_asset_sha256"]),
                          (snapshot, hashes["site_asset_sha256"])):
        for name, expected in entries.items():
            path = base / name
            if Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink() \
                    or not path.is_file() or digest(path) != expected:
                raise ValueError(f"Asset integrity failure: {name}")
    actual = {str(path.relative_to(snapshot)) for path in snapshot.rglob("*") if path.is_file()}
    if actual != {*hashes["site_asset_sha256"], "asset-hashes.json"}:
        raise ValueError("Unlisted snapshot asset")
    codes = {}
    details = {}
    for kind in ("constituencies", "organisations"):
        entry = release["indexes"][kind]
        if entry["path"] != f"snapshots/{release['snapshot_id']}/indexes/{kind}.json" \
                or digest(root / entry["path"]) != entry["sha256"]:
            raise ValueError("Index hash/path mismatch")
        rows = json.loads((root / entry["path"]).read_bytes())
        codes[kind] = {row[0] for row in rows}
        if len(rows) != len(codes[kind]) or len(rows) != release["counts"][kind]:
            raise ValueError("Index count mismatch")
        details[kind] = []
        for code, *_ in rows:
            path = snapshot / build.detail_relative(kind, code, release.get("detail_layout"))
            record = json.loads(path.read_bytes())
            expected_kind = "constituency" if kind == "constituencies" else "organisation"
            if record["code"] != code or record["kind"] != expected_kind:
                raise ValueError("Index/detail identity mismatch")
            details[kind].append(record)
    def target(kind: str, code: str | None) -> None:
        if code and code not in codes[kind]:
            raise ValueError(f"Missing relationship target: {code}")
    for record in details["constituencies"]:
        for row in [*record["sites"], *record["operators"],
                    *(row for row in record["practices"] if row["profile_available"])]:
            target("organisations", row["org_code"])
        for row in record["practices"]:
            target("constituencies", row.get("address_pcon24cd"))
    for record in details["organisations"]:
        target("constituencies", record["address"]["pcon24cd"])
        if record["operator"]["profile_available"]:
            target("organisations", record["operator"]["code"])
        if record["operator"]["current_at_snapshot"]:
            target("constituencies", record["operator"]["current_address_pcon24cd"])
        for row in record["served_constituencies"]:
            target("constituencies", row["code"])
    files = [path for path in root.rglob("*") if path.is_file()]
    directories = Counter(str(path.parent.relative_to(root)) for path in files)
    largest, count = directories.most_common(1)[0]
    if count > build.NETLIFY_DIRECTORY_LIMIT:
        raise ValueError("Netlify directory limit exceeded")
    return {"snapshot_id": release["snapshot_id"], "release_sha256": digest(root / "release.json"),
            "source_manifest_sha256": release["source_manifest_sha256"],
            "synthetic": release.get("synthetic", False), "counts": release["counts"],
            "file_count": len(files), "bytes": sum(path.stat().st_size for path in files),
            "largest_directory": largest, "largest_directory_files": count,
            "verified_relationship_targets": True}


def _store(store: Path) -> None:
    if store.is_symlink():
        raise ValueError("Symlink release store")
    store.mkdir(parents=True, exist_ok=True)
    marker = store / STORE_MARKER
    if not marker.exists():
        if any(store.iterdir()):
            raise ValueError("Unowned release store")
        marker.write_text("nhs-lens-loopback-store-v1\n")
    if marker.read_text() != "nhs-lens-loopback-store-v1\n":
        raise ValueError("Invalid release store marker")
    (store / "candidates").mkdir(exist_ok=True)


def install(archive: Path, expected_sha: str, store: Path) -> dict:
    """Stage an externally pinned archive, verify it, then retain it immutably."""
    if not SHA.fullmatch(expected_sha) or digest(archive) != expected_sha:
        raise ValueError("Archive SHA-256 mismatch")
    _store(store)
    destination = store / "candidates" / expected_sha
    if destination.exists():
        return verify_site(destination)
    stage = Path(mkdtemp(prefix=".install-", dir=store))
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            names = set()
            if len(members) > 100_000 or sum(item.size for item in members) > 1_000_000_000:
                raise ValueError("Archive capacity safety limit exceeded")
            for item in members:
                name = Path(item.name)
                if name.is_absolute() or ".." in name.parts or item.name in names \
                        or not (item.isfile() or item.isdir()):
                    raise ValueError("Unsafe archive entry")
                names.add(item.name)
            bundle.extractall(stage, filter="data")
        evidence = verify_site(stage)
        stage.rename(destination)
        return evidence
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def pin(store: Path, archive_sha: str) -> dict:
    """Replace one small pointer after full verification; keep previous candidates."""
    _store(store)
    if not SHA.fullmatch(archive_sha):
        raise ValueError("Invalid candidate SHA")
    evidence = verify_site(store / "candidates" / archive_sha)
    pointer = {"archive_sha256": archive_sha, "snapshot_id": evidence["snapshot_id"]}
    temporary = None
    try:
        with NamedTemporaryFile(dir=store, prefix=".pin-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(build._encoded(pointer))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(store / "current.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return pointer


def preview_server(store: Path, user: str, password: str, port: int = 0) -> ThreadingHTTPServer:
    """Loopback rehearsal only. A remote host must use TLS and approved access control."""
    if not user or not password:
        raise ValueError("Preview credentials are required")
    _store(store)
    token = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not retain credentials, queries or participant IP addresses.

        def do_HEAD(self):
            self.do_GET(head=True)

        def do_GET(self, head=False):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), token):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Local Lens rehearsal"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("Vary", "Authorization")
                self.end_headers()
                return
            url = urlsplit(self.path)
            name = unquote(url.path).lstrip("/")
            if not name:
                self.send_response(302)
                self.send_header("Location", "/index.html" + ("?" + url.query if url.query else ""))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            try:
                pointer = json.loads((store / "current.json").read_bytes())
                candidate = store / "candidates" / pointer["archive_sha256"]
                if name.startswith("snapshots/"):
                    # Retain assets requested by a browser which opened a previous pin.
                    candidates = sorted((store / "candidates").iterdir())
                    candidate = next((root for root in candidates if (root / name).is_file()), candidate)
                path = candidate / name
                if ".." in Path(name).parts or Path(name).is_absolute() or path.is_symlink() \
                        or not path.resolve().is_relative_to(candidate.resolve()) \
                        or name not in {*build.STATIC_FILES, "release.json"} and not name.startswith("snapshots/") \
                        or not path.is_file():
                    raise FileNotFoundError(name)
                content = path.read_bytes()
            except (OSError, ValueError, KeyError):
                self.send_error(404, "Asset unavailable in the pinned candidate")
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
            self.send_header("Cache-Control", "private, max-age=31536000, immutable"
                             if name.startswith("snapshots/") else "no-cache")
            self.send_header("Vary", "Authorization, Accept-Encoding")
            self.send_header("ETag", f'"{hashlib.sha256(content).hexdigest()}"')
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if "gzip" in self.headers.get("Accept-Encoding", "") and len(content) > 512:
                content = gzip.compress(content, mtime=0)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if not head:
                self.wfile.write(content)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--site", type=Path, required=True)
    stage = commands.add_parser("install")
    stage.add_argument("--archive", type=Path, required=True)
    stage.add_argument("--sha256", required=True)
    stage.add_argument("--store", type=Path, required=True)
    select = commands.add_parser("pin")
    select.add_argument("--candidate", required=True)
    select.add_argument("--store", type=Path, required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--store", type=Path, required=True)
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "verify":
        result = verify_site(args.site)
    elif args.command == "install":
        result = install(args.archive, args.sha256, args.store)
    elif args.command == "pin":
        result = pin(args.store, args.candidate)
    else:
        server = preview_server(args.store, os.environ.get("LENS_PREVIEW_USER", ""),
                                os.environ.get("LENS_PREVIEW_PASSWORD", ""), args.port)
        print(f"Protected loopback rehearsal: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
        return
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
