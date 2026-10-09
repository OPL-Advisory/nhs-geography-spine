"""Pinned public-source downloads and byte-level provenance."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
import yaml

CORE_SOURCE_KEYS = (
    "ons_lsoa21_pcon24",
    "ods_gp_practices",
    "nhs_postcode_directory",
    "gp_registered_patients_lsoa",
)
PROVIDER_SOURCE_KEYS = (
    "ods_gp_branches",
    "ods_nhs_trusts",
    "ods_nhs_trust_sites",
    "ods_other",
    "ods_sub_icb_locations",
    "ods_sub_icb_sites",
    "ons_icb26_codes",
)
SOURCE_KEYS = CORE_SOURCE_KEYS + PROVIDER_SOURCE_KEYS


@dataclass(frozen=True)
class Source:
    key: str
    url: str
    filename: str
    source_version: str
    source_date: str
    publisher: str
    source_date_kind: str | None = None
    data_member: str | None = None
    pcon_member: str | None = None


def load_config(path: Path) -> dict[str, Source]:
    """Load the explicit source contract, rejecting missing or unsafe entries."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) not in (set(CORE_SOURCE_KEYS), set(SOURCE_KEYS)):
        raise ValueError(f"{path}: expected the v0.1 sources or the complete v0.2 source set: {SOURCE_KEYS}")
    result: dict[str, Source] = {}
    for key in SOURCE_KEYS:
        if key not in data:
            continue
        item = data[key]
        required = ("url", "filename", "source_version", "source_date", "publisher")
        if not isinstance(item, dict) or any(not item.get(k) for k in required):
            raise ValueError(f"{path}: {key} is missing a required source field")
        filename = item["filename"]
        if Path(filename).name != filename or filename.startswith("."):
            raise ValueError(f"{path}: unsafe filename for {key}: {filename}")
        if urlparse(item["url"]).scheme not in {"https", "file"}:
            raise ValueError(f"{path}: {key} requires an HTTPS or local file URL")
        required_members = ("data_member", "pcon_member") if key == "nhs_postcode_directory" else (
            ("data_member",) if key == "gp_registered_patients_lsoa" else ()
        )
        if any(not item.get(member) for member in required_members):
            raise ValueError(f"{path}: {key} is missing required ZIP member names: {required_members}")
        result[key] = Source(key=key, **item)
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_download(path: Path, source: Source) -> None:
    if path.stat().st_size == 0:
        raise ValueError(f"{source.key}: empty download")
    if Path(source.filename).suffix.lower() == ".zip":
        if not zipfile.is_zipfile(path):
            raise ValueError(f"{source.key}: expected ZIP; upstream may have returned an error page")
        try:
            with zipfile.ZipFile(path) as archive:
                for member in (source.data_member, source.pcon_member):
                    if member and member not in archive.namelist():
                        raise ValueError(f"{source.key}: ZIP is missing expected member {member}")
                corrupt_member = archive.testzip()
        except (zipfile.BadZipFile, EOFError) as exc:
            raise ValueError(f"{source.key}: invalid ZIP archive") from exc
        if corrupt_member:
            raise ValueError(f"{source.key}: ZIP member failed integrity check: {corrupt_member}")
    else:
        with path.open("rb") as stream:
            head = stream.read(128).lstrip().lower()
        if head.startswith((b"<!doctype", b"<html", b"{")):
            raise ValueError(f"{source.key}: expected CSV; upstream returned another format")


def _download(source: Source, target: Path) -> None:
    parsed = urlparse(source.url)
    if parsed.scheme == "file":
        with Path(unquote(parsed.path)).open("rb") as stream, target.open("wb") as out:
            shutil.copyfileobj(stream, out)
        return
    try:
        with (
            httpx.Client(follow_redirects=True, timeout=180.0) as client,
            client.stream("GET", source.url, headers={"User-Agent": "nhs-geography-spine/0.1"}) as response,
        ):
            response.raise_for_status()
            with target.open("wb") as out:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"{source.key}: download failed from {source.url}: {exc}") from exc


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def fetch_sources(config: dict[str, Source], raw_dir: Path, refresh: bool = False) -> dict:
    """Download missing/changed inputs and record provenance for every raw file."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = raw_dir / "sources_manifest.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {"sources": {}}
    records = ledger.get("sources", {})
    for key, source in config.items():
        path = raw_dir / source.filename
        old = records.get(key, {})
        cached = (
            not refresh and path.exists() and old.get("url") == source.url
            and old.get("source_version") == source.source_version
            and (source.source_date == "retrieval" or old.get("source_date") == source.source_date)
            and old.get("sha256") == sha256_file(path)
        )
        if cached:
            _validate_download(path, source)
            continue
        temporary = raw_dir / (source.filename + ".partial")
        try:
            _download(source, temporary)
            _validate_download(temporary, source)
            digest = sha256_file(temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        retrieved = datetime.now(UTC).isoformat()
        records[key] = {
            "url": source.url,
            "publisher": source.publisher,
            "source_version": source.source_version,
            "source_date": retrieved[:10] if source.source_date == "retrieval" else source.source_date,
            "source_date_kind": source.source_date_kind,
            "retrieved_at": retrieved,
            "sha256": digest,
            "size_bytes": path.stat().st_size,
            "raw_path": str(path),
        }
        _write_json(ledger_path, {"sources": records})
    return {"sources": records}


def verified_sources(config: dict[str, Source], raw_dir: Path) -> dict:
    """Verify all cached inputs against their saved hash without network access."""
    ledger_path = raw_dir / "sources_manifest.json"
    if not ledger_path.exists():
        raise FileNotFoundError(f"{ledger_path} is missing; run `nhs-geo fetch` first")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    for key, source in config.items():
        record = ledger.get("sources", {}).get(key)
        path = raw_dir / source.filename
        if not record or not path.exists() or record.get("url") != source.url:
            raise FileNotFoundError(f"{key}: cached source missing or URL changed; run `nhs-geo fetch`")
        if record.get("source_version") != source.source_version or record.get("sha256") != sha256_file(path):
            raise ValueError(f"{key}: cached source version/hash changed; run `nhs-geo fetch --refresh`")
        if source.source_date != "retrieval" and record.get("source_date") != source.source_date:
            raise ValueError(f"{key}: cached source date changed; run `nhs-geo fetch`")
        _validate_download(path, source)
    return ledger
