"""Generate fictional journeys and a reproducible full-size hosting rehearsal.

This makes a presentation-contract fixture, not an accepted real-source pipeline
output. X/Z codes, invented names and the synthetic banner prevent confusion.
"""

from __future__ import annotations

import argparse
import random
import string
from pathlib import Path

from web import build, release

PERIOD = "2026-01-01"
PCON = "X14000001"
OTHER = "X14000002"
GP = "XG00001"
TRUST = "XT00001"
ICB = "XI00001"
INACTIVE = "XB00001"
BRANCH = "XR00001"
EMPTY = "XE00001"
UNRESOLVED = "XU00001"


def _organisation(code: str, name: str, kind: str = "nhs_trust") -> dict:
    return {
        "org_code": code, "org_name": name, "organisation_type": kind, "status": "ACTIVE",
        "source_snapshot_date": PERIOD, "source_version": "Synthetic fixture; no official data",
        "address_pcon24cd": PCON, "address_pcon24nm": "Demo Harbour",
        "address_member_name": "Demo Member", "address_member_status": "current",
        "member_source_snapshot_date": PERIOD, "mapping_method": "postcode_direct",
        "unmapped_reason": None, "parent_org_code": None, "parent_org_name": None,
        "relationship_type": None, "parent_relationship_temporal_status": None,
        "parent_relationship_current_at_snapshot": False, "relationship_start_date": None,
        "relationship_end_date": None, "parent_address_pcon24cd": None,
        "parent_current_address_pcon24cd": None, "parent_address_member_name": None,
        "parent_address_member_status": None, "registered_patients_total": None,
        "unmapped_patient_count": 0, "patient_source_period": None, "served_constituencies": [],
    }


def _relationship(code: str, name: str, kind: str, basis: str, **kwargs) -> dict:
    return {"org_code": code, "org_name": name, "organisation_type": kind,
            "relationship_basis": basis, "source_snapshot_date": PERIOD,
            "evidence_source_version": "Synthetic fixture", **kwargs}


def generate(inputs: Path, destination: Path, *, organisation_count: int = 64_040,
             constituency_count: int = 650, variant: int = 1, padding: int = 2304) -> dict:
    if organisation_count < 7 or constituency_count < 4 or padding < 0:
        raise ValueError("At least seven organisations and four constituencies are required")
    if inputs.exists() and any(inputs.iterdir()):
        raise ValueError("Use an empty synthetic input directory")
    if inputs.resolve().is_relative_to(destination.resolve()) \
            or destination.resolve().is_relative_to(inputs.resolve()):
        raise ValueError("Synthetic input/output directories must be separate")
    inputs.mkdir(parents=True, exist_ok=True)
    gp = _organisation(GP, "Demo Cross Boundary Practice", "gp_practice")
    gp.update(registered_patients_total=100, unmapped_patient_count=10,
              patient_source_period=PERIOD,
              served_constituencies=[
                  {"pcon24cd": PCON, "pcon24nm": "Demo Harbour", "registered_patients": 30,
                   "share_of_practice_list": .3, "member_name": "Demo Member", "member_status": "current"},
                  {"pcon24cd": OTHER, "pcon24nm": "Demo Vale", "registered_patients": 60,
                   "share_of_practice_list": .6, "member_name": None, "member_status": "vacant"}])
    trust = _organisation(TRUST, "Demo Harbour Trust")
    icb = _organisation(ICB, "Demo Integrated Care Board", "integrated_care_board")
    inactive = _organisation(INACTIVE, "Demo Inactive Site", "nhs_trust_site")
    inactive.update(status="INACTIVE", parent_org_code=TRUST, parent_org_name=trust["org_name"],
                    relationship_type="RE6", parent_relationship_temporal_status="effective",
                    relationship_start_date="2020-01-01", parent_address_pcon24cd=PCON)
    branch = _organisation(BRANCH, "Demo Operated Site", "nhs_trust_site")
    branch.update(parent_org_code=TRUST, parent_org_name=trust["org_name"], relationship_type="RE6",
                  parent_relationship_temporal_status="effective",
                  parent_relationship_current_at_snapshot=True, relationship_start_date="2020-01-01",
                  parent_address_pcon24cd=PCON, parent_current_address_pcon24cd=PCON,
                  parent_address_member_name="Demo Member", parent_address_member_status="current")
    empty = _organisation(EMPTY, "Demo Practice Without Patient Extract", "gp_practice")
    empty.update(address_pcon24cd=None, address_pcon24nm=None, address_member_name=None,
                 address_member_status=None, unmapped_reason="Synthetic missing postcode",
                 mapping_method="unmapped")
    unresolved = _organisation(UNRESOLVED, "Demo Unresolved Operator", "nhs_trust_site")
    unresolved.update(parent_org_code="XY00000", relationship_type="RE6",
                      parent_relationship_temporal_status="effective",
                      parent_relationship_current_at_snapshot=True)
    for record in (gp, trust, icb, inactive, branch, empty, unresolved):
        if record["address_member_name"]:
            record["address_member_name"] = f"Demo Member v{variant}"
        if record["parent_address_member_name"]:
            record["parent_address_member_name"] = f"Demo Member v{variant}"
        for served in record["served_constituencies"]:
            if served["member_name"]:
                served["member_name"] = f"Demo Member v{variant}"
        build._write_json(inputs / "json" / "organisations" / f"{record['org_code']}.json", record)
    for index in range(organisation_count - 7):
        code = f"Z{index:05d}"
        rng = random.Random(index)
        entropy = "".join(rng.choices(string.ascii_lowercase, k=384))
        record = _organisation(code, f"Synthetic organisation {code} {entropy[:14]}")
        record["address_member_name"] = f"Demo Member v{variant}"
        record["status"] = "INACTIVE"
        record["source_version"] += ": " + (entropy * (padding // len(entropy) + 1))[:padding]
        build._write_json(inputs / "json" / "organisations" / f"{code}.json", record)
    for index in range(1, constituency_count + 1):
        code = f"X140{index:05d}"
        name = {1: "Demo Harbour", 2: "Demo Vale", 3: "Demo Empty", 4: "Demo Partial"}.get(
            index, f"Synthetic constituency {index:03d}")
        practices = []
        if index in (1, 2):
            count = 30 if index == 1 else 60
            practices = [_relationship(GP, gp["org_name"], "gp_practice", "registered_patients",
                          registered_patients=count, share_of_practice_list=count / 100,
                          patient_source_period=PERIOD, address_pcon24cd=PCON)]
        if index == 4:
            practices = [_relationship("XP99999", "Demo Patient-only Practice", "gp_practice",
                         "registered_patients", registered_patients=7, share_of_practice_list=1,
                         patient_source_period=PERIOD, address_pcon24cd=None)]
        sites = [_relationship(record["org_code"], record["org_name"], record["organisation_type"],
                               "site_location") for record in (gp, trust, icb, branch, unresolved)] \
            if index == 1 else []
        operators = [_relationship(BRANCH, branch["org_name"], "nhs_trust_site", "operating_relationship",
                                   parent_org_code=TRUST, relationship_temporal_status="effective")] \
            if index == 1 else []
        record = {"pcon24cd": code, "pcon24nm": name,
                  "member_name": None if index == 2 else f"Demo Member v{variant}",
                  "party_name": None if index == 2 else "Fictional Party", "member_status":
                  "vacant" if index == 2 else "current", "source_snapshot_date": PERIOD,
                  "site_counts_by_type": {}, "site_organisation_count": len(sites),
                  "serving_gp_practice_count": len(practices), "registered_patients_mapped":
                  sum(row["registered_patients"] for row in practices), "patient_source_period": PERIOD,
                  "site_organisations": sites, "serving_gp_practices": practices,
                  "operating_relationships": operators}
        build._write_json(inputs / "json" / "constituencies" / f"{code}.json", record)
    manifest = {"synthetic": True, "fixture_variant": variant,
                "code": {"git_sha": "synthetic-presentation-contract"}, "build_timestamp": PERIOD,
                "row_counts": {"pcon24_code_rows": constituency_count,
                               "all_ods_organisation_rows": organisation_count},
                "sources": {"gp_registered_patients_lsoa": {"source_date": PERIOD},
                            "ods_synthetic": {"source_date": PERIOD}},
                "geography_vintages": {"pcon": "Synthetic July 2024-shaped codes",
                                       "postcode": "Synthetic directory; no real postcodes"},
                "qa_summary": {"provider_active_unmapped_rows": 1,
                               "provider_unresolved_active_re6_relationships": 1}}
    build._write_json(inputs / "build_manifest.json", manifest)
    build._write_json(inputs / "qa_report.json", {
        "status": "pass", "qa_basis": "Synthetic presentation fixture only; not source QA",
        "parliamentary": {"member_source_snapshot_date": PERIOD},
        "patient_reconciliation": {"source_total": 107, "mapped_total": 97,
                                   "unmapped_total": 10, "difference": 0}})
    build.build_site(inputs, destination, validate=False)
    return release.verify_site(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--organisations", type=int, default=64_040)
    parser.add_argument("--constituencies", type=int, default=650)
    parser.add_argument("--variant", type=int, default=1)
    parser.add_argument("--padding", type=int, default=2304)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    evidence = generate(args.inputs, args.output_dir, organisation_count=args.organisations,
                        constituency_count=args.constituencies, variant=args.variant, padding=args.padding)
    if args.archive:
        evidence["archive_sha256"] = build.make_archive(args.output_dir, args.archive)
        evidence["archive_bytes"] = args.archive.stat().st_size
    print(build._encoded(evidence).decode(), end="")


if __name__ == "__main__":
    main()
