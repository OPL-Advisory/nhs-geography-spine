"""Build a pinned, read-only Parliamentary Lens site from accepted v0.3 JSON."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path
from tempfile import mkdtemp

from nhs_geo_spine.qa import audit_outputs

HERE = Path(__file__).resolve().parent
MARKER = ".nhs-parliamentary-lens-output"
MARKER_TEXT = "nhs-parliamentary-lens-static-v1\n"
CODE = re.compile(r"^[A-Z0-9]+$")
STATIC_FILES = ("index.html", "app.js", "search.mjs", "styles.css")


def _encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: object) -> str:
    content = _encoded(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return _hash(content)


def _relationship(row: dict, *, patient: bool = False) -> dict:
    result = {
        "org_code": row["org_code"], "org_name": row.get("org_name"),
        "organisation_type": row.get("organisation_type"),
        "basis": row["relationship_basis"],
        "source_snapshot_date": row.get("source_snapshot_date"),
        "source_version": row.get("evidence_source_version"),
    }
    if patient:
        result.update(
            registered_patients=row["registered_patients"],
            share_of_practice_list=row["share_of_practice_list"],
            patient_source_period=row["patient_source_period"],
            address_pcon24cd=row.get("address_pcon24cd"),
        )
    else:
        result["parent_org_code"] = row.get("parent_org_code")
        result["relationship_temporal_status"] = row.get("relationship_temporal_status")
    return result


def _constituency(source: dict) -> dict:
    for field, basis in (("site_organisations", "site_location"),
                         ("serving_gp_practices", "registered_patients"),
                         ("operating_relationships", "operating_relationship")):
        if any(row["relationship_basis"] != basis for row in source[field]):
            raise ValueError(f"Unexpected {field} evidence basis in {source['pcon24cd']}")
    practices = sorted(source["serving_gp_practices"],
                       key=lambda row: (-row["registered_patients"], row["org_code"]))
    return {
        "kind": "constituency", "code": source["pcon24cd"], "name": source["pcon24nm"],
        "member": {"name": source["member_name"], "party": source["party_name"],
                   "status": source["member_status"], "snapshot_date": source["source_snapshot_date"]},
        "site_counts_by_type": source["site_counts_by_type"],
        "site_organisation_count": source["site_organisation_count"],
        "serving_gp_practice_count": source["serving_gp_practice_count"],
        "registered_patients_mapped": source["registered_patients_mapped"],
        "patient_source_period": source["patient_source_period"],
        "sites": [_relationship(row) for row in source["site_organisations"]],
        "practices": [_relationship(row, patient=True) for row in practices],
        "operators": [_relationship(row) for row in source["operating_relationships"]],
    }


def _organisation(source: dict) -> dict:
    served = sorted(source["served_constituencies"] or [],
                    key=lambda row: (-row["registered_patients"], row["pcon24cd"]))
    if source["organisation_type"] != "gp_practice" and served:
        raise ValueError(f"Non-GP patient links in {source['org_code']}")
    return {
        "kind": "organisation", "code": source["org_code"], "name": source["org_name"],
        "organisation_type": source["organisation_type"], "status": source["status"],
        "source_snapshot_date": source["source_snapshot_date"],
        "source_version": source["source_version"],
        "address": {
            "pcon24cd": source["address_pcon24cd"],
            "pcon24nm": source["address_pcon24nm"],
            "member_name": source["address_member_name"],
            "member_status": source["address_member_status"],
            "member_snapshot_date": source["member_source_snapshot_date"],
            "mapping_method": source["mapping_method"],
            "unmapped_reason": source["unmapped_reason"],
        },
        "operator": {
            "code": source["parent_org_code"], "name": source["parent_org_name"],
            "relationship_type": source["relationship_type"],
            "temporal_status": source["parent_relationship_temporal_status"],
            "current_at_snapshot": source["parent_relationship_current_at_snapshot"],
            "start_date": source["relationship_start_date"],
            "end_date": source["relationship_end_date"],
            "source_address_pcon24cd": source["parent_address_pcon24cd"],
            "current_address_pcon24cd": source["parent_current_address_pcon24cd"],
            "current_address_member_name": source["parent_address_member_name"],
            "current_address_member_status": source["parent_address_member_status"],
        },
        "registered_patients_total": source["registered_patients_total"],
        "unmapped_patient_count": source["unmapped_patient_count"],
        "patient_source_period": source["patient_source_period"],
        "served_constituencies": [{
            "code": row["pcon24cd"], "name": row["pcon24nm"],
            "registered_patients": row["registered_patients"],
            "share_of_practice_list": row["share_of_practice_list"],
            "member_name": row["member_name"], "member_status": row["member_status"],
        } for row in served],
    }


def _check_destination(destination: Path, processed: Path) -> None:
    target = destination.resolve()
    if (target == Path.cwd().resolve() or target.is_relative_to(processed.resolve())
            or processed.resolve().is_relative_to(target) or destination.is_symlink()):
        raise ValueError(f"Unsafe site destination: {destination}")
    if not destination.exists():
        return
    if not destination.is_dir():
        raise ValueError(f"Site destination is not a directory: {destination}")
    entries = list(destination.iterdir())
    if entries and ((destination / MARKER).read_text(encoding="utf-8")
                    if (destination / MARKER).is_file() else None) != MARKER_TEXT:
        raise ValueError(f"Unowned site destination: {destination}")
    allowed = {*STATIC_FILES, "release.json", "snapshots", MARKER}
    if any(entry.name not in allowed or entry.is_symlink() for entry in entries):
        raise ValueError(f"Unowned file in site destination: {destination}")
    if entries and any(not (destination / name).is_file()
                       for name in (*STATIC_FILES, "release.json", MARKER)):
        raise ValueError(f"Incomplete site destination: {destination}")
    snapshots = destination / "snapshots"
    if snapshots.exists():
        if not snapshots.is_dir() or snapshots.is_symlink():
            raise ValueError(f"Unsafe snapshots directory: {snapshots}")
        release_file = destination / "release.json"
        if not release_file.is_file():
            raise ValueError(f"Missing release record in site destination: {destination}")
        release = json.loads(release_file.read_text(encoding="utf-8"))
        snapshot_id = release.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
            raise ValueError(f"Invalid snapshot record in site destination: {destination}")
        expected = {
            "asset-hashes.json",
            "indexes/constituencies.json", "indexes/organisations.json",
        }
        counts = release.get("counts", {})
        for kind in ("constituencies", "organisations"):
            folder = snapshots / snapshot_id / "details" / kind
            if not folder.is_dir() or folder.is_symlink():
                raise ValueError(f"Missing site detail folder: {folder}")
            codes = {f"details/{kind}/{path.name}" for path in folder.glob("*.json")}
            if len(codes) != counts.get(kind):
                raise ValueError(f"Site detail count differs from release: {folder}")
            expected.update(codes)
        snapshot = snapshots / snapshot_id
        found = {str(path.relative_to(snapshot)) for path in snapshot.rglob("*") if path.is_file()}
        directories = {str(path.relative_to(snapshot)) for path in snapshot.rglob("*")
                       if path.is_dir()}
        if found != expected or any(path.is_symlink() for path in snapshots.rglob("*")) \
                or directories != {"indexes", "details", "details/constituencies",
                                   "details/organisations"} \
                or {path.name for path in snapshots.iterdir()} != {snapshot_id}:
            raise ValueError(f"Unowned file in site destination: {destination}")


def _publish(stage: Path, destination: Path) -> None:
    backup = destination.with_name(f".{destination.name}-previous")
    if backup.exists():
        raise ValueError(f"Previous site backup needs inspection: {backup}")
    if destination.exists():
        destination.replace(backup)
    try:
        stage.replace(destination)
    except Exception:
        if backup.exists():
            backup.replace(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def build_site(processed: Path, destination: Path, *, validate: bool = True) -> dict:
    """Validate, project and atomically publish a deterministic static artifact."""
    processed = processed.resolve()
    _check_destination(destination, processed)
    manifest_path = processed / "build_manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    qa = audit_outputs(processed) if validate else json.loads(
        (processed / "qa_report.json").read_text(encoding="utf-8"))
    if qa["status"] not in {"pass", "pass_with_warnings"} or "parliamentary" not in qa:
        raise ValueError("A validated v0.3 parliamentary bundle is required")
    counts = manifest["row_counts"]
    sources = manifest["sources"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(mkdtemp(prefix=f".{destination.name}-build-", dir=destination.parent))
    try:
        static_hashes: dict[str, str] = {}
        for name in STATIC_FILES:
            shutil.copyfile(HERE / name, stage / name)
            static_hashes[name] = _hash((stage / name).read_bytes())
        (stage / MARKER).write_text(MARKER_TEXT, encoding="utf-8")
        working = stage / "snapshots" / "pending"
        source_hashes: dict[str, str] = {}
        asset_hashes: dict[str, str] = {}
        index: dict[str, list] = {"constituencies": [], "organisations": []}
        for kind, folder, expected, projector in (
            ("constituencies", "constituencies", counts["pcon24_code_rows"], _constituency),
            ("organisations", "organisations", counts["all_ods_organisation_rows"], _organisation),
        ):
            paths = sorted((processed / "json" / folder).glob("*.json"))
            if len(paths) != expected:
                raise ValueError(f"{folder} count differs from accepted manifest")
            seen: set[str] = set()
            for path in paths:
                content = path.read_bytes()
                source = json.loads(content)
                detail = projector(source)
                code = detail["code"]
                if code != path.stem or not CODE.fullmatch(code) or code in seen:
                    raise ValueError(f"Invalid or duplicate {kind} code: {path}")
                seen.add(code)
                relative = f"details/{folder}/{code}.json"
                source_hashes[f"json/{folder}/{code}.json"] = _hash(content)
                asset_hashes[relative] = _write_json(working / relative, detail)
                if kind == "constituencies":
                    index[kind].append([code, detail["name"]])
                else:
                    index[kind].append([code, detail["name"], detail["organisation_type"]])
            index[kind].sort(key=lambda row: (row[1].casefold(), row[0]))
            relative = f"indexes/{kind}.json"
            asset_hashes[relative] = _write_json(working / relative, index[kind])
        hash_manifest = {"source_manifest_sha256": _hash(manifest_bytes),
                         "source_json_sha256": source_hashes,
                         "static_asset_sha256": static_hashes,
                         "site_asset_sha256": asset_hashes}
        hash_bytes = _encoded(hash_manifest)
        snapshot_id = _hash(hash_bytes)
        (working / "asset-hashes.json").write_bytes(hash_bytes)
        working.rename(stage / "snapshots" / snapshot_id)
        ods_dates = sorted({record["source_date"] for key, record in sources.items()
                            if key.startswith("ods_")})
        release = {
            "schema_version": 1, "snapshot_id": snapshot_id,
            "source_manifest_sha256": _hash(manifest_bytes),
            "static_asset_sha256": static_hashes,
            "source_code_sha": manifest["code"]["git_sha"],
            "source_build_timestamp": manifest["build_timestamp"],
            "counts": {"constituencies": len(index["constituencies"]),
                       "organisations": len(index["organisations"])},
            "dates": {
                "parliament": qa["parliamentary"]["member_source_snapshot_date"],
                "gp_patients": sources["gp_registered_patients_lsoa"]["source_date"],
                "ods": ods_dates,
                "pcon": manifest["geography_vintages"]["pcon"],
                "postcode": manifest["geography_vintages"]["postcode"],
            },
            "qa_status": qa["status"],
            "coverage": {
                "active_unmapped_provider_addresses": manifest["qa_summary"].get(
                    "provider_active_unmapped_rows", 0),
                "unresolved_active_re6_links": manifest["qa_summary"].get(
                    "provider_unresolved_active_re6_relationships", 0),
                "unmapped_registered_patients": qa["patient_reconciliation"]["unmapped_total"],
            },
            "indexes": {kind: {"path": f"snapshots/{snapshot_id}/indexes/{kind}.json",
                               "sha256": asset_hashes[f"indexes/{kind}.json"]}
                        for kind in index},
            "details_base": f"snapshots/{snapshot_id}/details",
            "asset_hashes_path": f"snapshots/{snapshot_id}/asset-hashes.json",
            "asset_hashes_sha256": _hash(hash_bytes),
        }
        _write_json(stage / "release.json", release)
        _publish(stage, destination)
        return release
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def make_archive(destination: Path, archive: Path) -> str:
    """Package an immutable candidate with repeatable bytes for rollback."""
    if not (destination / MARKER).is_file():
        raise ValueError("Build the site before making an archive")
    if archive.resolve().is_relative_to(destination.resolve()):
        raise ValueError("Archive must be outside the site artifact")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("wb") as out, \
            gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as compressed, \
            tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        for path in sorted(destination.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Symlink in site artifact: {path}")
            info = bundle.gettarinfo(str(path), arcname=str(path.relative_to(destination)))
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if path.is_file():
                with path.open("rb") as stream:
                    bundle.addfile(info, stream)
            else:
                bundle.addfile(info)
    return _hash(archive.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--output-dir", type=Path, default=HERE / "dist")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    release = build_site(args.processed_dir, args.output_dir)
    print(f"Built snapshot {release['snapshot_id']} with {release['counts']}")
    if args.archive:
        print(f"Archive SHA-256: {make_archive(args.output_dir, args.archive)}")


if __name__ == "__main__":
    main()
