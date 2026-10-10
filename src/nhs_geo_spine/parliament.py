"""Current Commons members and evidence-labelled NHS parliamentary relationships."""

from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import polars as pl

MEMBER_SOURCE_KEY = "parliament_current_constituencies"
RELATIONSHIP_BASES = {"site_location", "registered_patients", "operating_relationship"}
PARLIAMENT_TABLES = ("dim_pcon_member", "pcon_parliamentary_brief",
                     "organisation_parliamentary_profile", "mp_nhs_relationship")
PARLIAMENT_OUTPUTS = tuple(
    f"{name}.{suffix}" for name in PARLIAMENT_TABLES
    for suffix in (("parquet",) if name == "dim_pcon_member" else ("parquet", "csv"))
)


def _name_key(value: str) -> str:
    """Bridge Parliament names to the fixed PCON24 names without hand-coded exceptions."""
    decomposed = unicodedata.normalize("NFKD", value)
    return " ".join("".join(character for character in decomposed
                            if not unicodedata.combining(character)).casefold().split())


def read_members(path: Path, pcon: pl.DataFrame, source: dict) -> pl.DataFrame:
    """Use the official current constituency list, including any vacant seats."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload["items"]
    if len(items) != payload["totalResults"]:
        raise ValueError("Parliament constituency snapshot is incomplete")
    pcon_by_name: dict[str, dict] = {}
    for row in pcon.to_dicts():
        key = _name_key(row["pcon24nm"])
        if key in pcon_by_name:
            raise ValueError(f"PCON24 names collide after normalization: {key}")
        pcon_by_name[key] = row
    rows = []
    seen_names: set[str] = set()
    seen_constituencies: set[int] = set()
    seen_members: set[int] = set()
    unmatched: list[str] = []
    for item in items:
        value = item.get("value") or {}
        name = value.get("name")
        constituency_id = value.get("id")
        if not isinstance(name, str) or not isinstance(constituency_id, int):
            raise TypeError("Parliament constituency is missing an ID or name")
        key = _name_key(name)
        if key in seen_names or constituency_id in seen_constituencies:
            raise ValueError(f"Duplicate Parliament constituency: {name}")
        seen_names.add(key)
        seen_constituencies.add(constituency_id)
        if value.get("endDate") is not None:
            raise ValueError(f"Parliament source returned an ended constituency: {name}")
        matching = pcon_by_name.get(key)
        if matching is None:
            unmatched.append(name)
            continue
        representation = value.get("currentRepresentation")
        member = ((representation or {}).get("member") or {}).get("value")
        member_id = member.get("id") if member else None
        member_name = member.get("nameDisplayAs") if member else None
        if member is not None:
            membership = member.get("latestHouseMembership") or {}
            if (not isinstance(member_id, int) or not member_name
                    or membership.get("house") != 1
                    or membership.get("membershipFromId") != constituency_id
                    or not (membership.get("membershipStatus") or {}).get("statusIsActive")):
                raise ValueError(f"Invalid current Commons member for {name}")
            if member_id in seen_members:
                raise ValueError(f"Member {member_id} is assigned to multiple constituencies")
            seen_members.add(member_id)
        rows.append({
            "pcon24cd": matching["pcon24cd"], "pcon24nm": matching["pcon24nm"],
            "parliament_constituency_id": constituency_id,
            "parliament_constituency_name": name,
            "member_id": member_id, "member_name": member_name,
            "party_name": (member.get("latestParty") or {}).get("name") if member else None,
            "member_status": "current" if member else "vacant",
            "membership_start_date": (
                date.fromisoformat(representation["representation"]["membershipStartDate"][:10])
                if member and (representation.get("representation") or {}).get("membershipStartDate")
                else None
            ),
            "source_snapshot_date": date.fromisoformat(source["source_date"]),
            "source": "UK Parliament Members API",
            "source_version": source["source_version"], "source_url": source["url"],
            "retrieved_at": source["retrieved_at"],
            "name_match_method": "unicode_normalized_name",
        })
    missing = sorted(set(pcon_by_name) - seen_names)
    if unmatched or missing:
        raise ValueError(f"Parliament/PCON24 name mismatch: Parliament-only {sorted(unmatched)[:10]}; "
                         f"PCON24-only {missing[:10]}")
    result = pl.DataFrame(rows).sort("pcon24cd")
    if result.height != pcon.height:
        raise ValueError("Parliament dimension does not cover the full PCON24 code set")
    return result


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str,
                      ensure_ascii=False)


def _member_fields(row: dict) -> dict:
    return {key: row.get(key) for key in ("member_id", "member_name", "party_name",
                                         "member_status", "source_snapshot_date")}


def make_parliamentary_tables(members: pl.DataFrame, org_profile: pl.DataFrame,
                              patients: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame,
                                                                pl.DataFrame]:
    """Create one relationship row per signal, without multiplying patient counts."""
    member_by_code = {r["pcon24cd"]: r for r in members.to_dicts()}
    org_by_code = {r["org_code"]: r for r in org_profile.to_dicts()}
    source_date = members["source_snapshot_date"][0]
    relationships: list[dict] = []

    def add(pcon_code: str, org_code: str, org_name: str | None, org_type: str,
            basis: str, address_code: str | None, patients_count: int | None,
            patient_share: float | None, period: str | None,
            parent_code: str | None, evidence_version: str | None,
            evidence_snapshot_date: date) -> None:
        member = member_by_code[pcon_code]
        relationships.append({
            "pcon24cd": pcon_code, "pcon24nm": member["pcon24nm"],
            "member_id": member["member_id"], "member_name": member["member_name"],
            "party_name": member["party_name"], "member_status": member["member_status"],
            "org_code": org_code, "org_name": org_name, "organisation_type": org_type,
            "relationship_basis": basis, "site_in_constituency": address_code == pcon_code,
            "address_pcon24cd": address_code, "parent_org_code": parent_code,
            "registered_patients": patients_count,
            "share_of_practice_list": patient_share,
            "patient_source_period": period if basis == "registered_patients" else None,
            "relationship_source_period": period,
            "source_snapshot_date": evidence_snapshot_date,
            "member_source_snapshot_date": source_date,
            "evidence_source_version": evidence_version,
        })

    for org in org_profile.iter_rows(named=True):
        if org["status"] != "ACTIVE":
            continue
        address = org["address_pcon24cd"]
        if address in member_by_code:
            add(address, org["org_code"], org["org_name"], org["organisation_type"],
                "site_location", address, None, None, str(org["source_snapshot_date"]), None,
                org["source_version"], org["source_snapshot_date"])
        parent_code = org["parent_org_code"]
        if org["relationship_type"] == "RE6" and parent_code:
            parent = org_by_code.get(parent_code)
            parent_address = parent["address_pcon24cd"] if parent else None
            if parent_address in member_by_code:
                add(parent_address, org["org_code"], org["org_name"],
                    org["organisation_type"], "operating_relationship", address,
                    None, None, str(org["relationship_start_date"] or org["source_snapshot_date"]),
                    parent_code, org["source_version"], org["source_snapshot_date"])

    served_by_org: dict[str, list[dict]] = defaultdict(list)
    unmapped_by_org: dict[str, int] = defaultdict(int)
    total_by_org: dict[str, int] = defaultdict(int)
    period_by_org: dict[str, str] = {}
    for patient in patients.iter_rows(named=True):
        code = patient["practice_code"]
        count = patient["patient_count"]
        total_by_org[code] += count
        period_by_org[code] = patient["lsoa_source_period"]
        pcon_code = patient["pcon24cd"]
        if pcon_code == "UNMAPPED":
            unmapped_by_org[code] += count
            continue
        org = org_by_code.get(code)
        address = org["address_pcon24cd"] if org else None
        member = member_by_code[pcon_code]
        served_by_org[code].append({
            "pcon24cd": pcon_code, "pcon24nm": member["pcon24nm"],
            "registered_patients": count,
            "share_of_practice_list": patient["patient_share"],
            **_member_fields(member),
        })
        add(pcon_code, code, org["org_name"] if org else None, "gp_practice",
            "registered_patients", address, count, patient["patient_share"],
            patient["lsoa_source_period"], None, patient["source_version"],
            date.fromisoformat(patient["lsoa_source_period"]))

    profiles = []
    for org in org_profile.iter_rows(named=True):
        code = org["org_code"]
        address = org["address_pcon24cd"]
        member = member_by_code.get(address)
        parent_member = member_by_code.get(org["parent_address_pcon24cd"])
        is_gp = org["organisation_type"] == "gp_practice"
        served = sorted(served_by_org.get(code, []), key=lambda r: r["pcon24cd"])
        profiles.append({
            **org,
            "address_member_id": member["member_id"] if member else None,
            "address_member_name": member["member_name"] if member else None,
            "address_member_party": member["party_name"] if member else None,
            "address_member_status": member["member_status"] if member else None,
            "parent_address_member_id": parent_member["member_id"] if parent_member else None,
            "parent_address_member_name": parent_member["member_name"] if parent_member else None,
            "parent_address_member_status": parent_member["member_status"] if parent_member else None,
            "parent_relationship_basis": "operating_relationship" if org["relationship_type"] == "RE6"
                                         else None,
            "registered_patients_total": total_by_org.get(code) if is_gp else None,
            "unmapped_patient_count": unmapped_by_org.get(code, 0) if code in total_by_org else None,
            "served_constituency_count": len(served) if is_gp else None,
            "served_constituencies_json": _json(served) if is_gp else None,
            "patient_source_period": period_by_org.get(code) if is_gp else None,
            "member_source_snapshot_date": source_date,
        })

    links = pl.DataFrame(relationships, infer_schema_length=None).sort(
        "pcon24cd", "relationship_basis", "org_code")
    profile = pl.DataFrame(profiles, infer_schema_length=None).sort("org_code")
    site_links = [r for r in relationships if r["relationship_basis"] == "site_location"]
    patient_links = [r for r in relationships if r["relationship_basis"] == "registered_patients"]
    sites_by_pcon: dict[str, list[dict]] = defaultdict(list)
    patients_by_pcon: dict[str, list[dict]] = defaultdict(list)
    for row in site_links:
        sites_by_pcon[row["pcon24cd"]].append(row)
    for row in patient_links:
        patients_by_pcon[row["pcon24cd"]].append(row)
    briefs = []
    for member in members.iter_rows(named=True):
        code = member["pcon24cd"]
        sites = sites_by_pcon[code]
        served = patients_by_pcon[code]
        counts = dict(sorted(Counter(r["organisation_type"] for r in sites).items()))
        briefs.append({
            "pcon24cd": code, "pcon24nm": member["pcon24nm"],
            **_member_fields(member),
            "site_organisation_count": len(sites),
            "site_counts_by_type_json": _json(counts),
            "serving_gp_practice_count": len(served),
            "registered_patients_mapped": sum(r["registered_patients"] for r in served),
            "site_evidence_basis": "site_location",
            "patient_evidence_basis": "registered_patients",
            "patient_source_period": next((r["patient_source_period"] for r in served), None),
        })
    return pl.DataFrame(briefs).sort("pcon24cd"), profile, links


def validate_parliamentary_tables(members: pl.DataFrame, brief: pl.DataFrame,
                                   profile: pl.DataFrame, links: pl.DataFrame,
                                   pcon: pl.DataFrame, org_profile: pl.DataFrame,
                                   patients: pl.DataFrame) -> dict:
    if (members["pcon24cd"].n_unique() != members.height
            or set(members["pcon24cd"]) != set(pcon["pcon24cd"])):
        raise ValueError("Member dimension fails PCON24 coverage or uniqueness")
    active = members.filter(pl.col("member_status") == "current")
    if active["member_id"].n_unique() != active.height:
        raise ValueError("Duplicate active Parliament member")
    if (set(members["member_status"]) - {"current", "vacant"}
            or active.filter(pl.col("member_id").is_null()
                             | pl.col("member_name").is_null()).height):
        raise ValueError("Invalid current Parliament member status or identity")
    if members.filter(pl.col("member_status") == "vacant").filter(
            pl.col("member_id").is_not_null()
            | pl.col("member_name").is_not_null()
            | pl.col("party_name").is_not_null()).height:
        raise ValueError("Vacancy has an assigned member")
    if members.filter(pl.col("source_snapshot_date").is_null()
                      | pl.col("source_version").is_null()
                      | pl.col("retrieved_at").is_null()).height:
        raise ValueError("Member source provenance is incomplete")
    if (brief["pcon24cd"].n_unique() != brief.height
            or set(brief["pcon24cd"]) != set(pcon["pcon24cd"])):
        raise ValueError("Parliamentary brief does not cover every PCON24 code")
    if (profile["org_code"].n_unique() != profile.height
            or set(profile["org_code"]) != set(org_profile["org_code"])):
        raise ValueError("Parliamentary profile does not cover every ODS code")
    if set(links["relationship_basis"]) - RELATIONSHIP_BASES:
        raise ValueError("Undocumented parliamentary relationship basis")
    if links.group_by("pcon24cd", "org_code", "relationship_basis").len().filter(
            pl.col("len") > 1).height:
        raise ValueError("Duplicate parliamentary relationship")
    if set(links["pcon24cd"]) - set(pcon["pcon24cd"]):
        raise ValueError("Parliamentary relationship contains an invalid PCON24 code")
    member_join = links.join(members.select(
        "pcon24cd", pl.col("member_id").alias("expected_member_id"),
        pl.col("member_status").alias("expected_member_status"),
    ), on="pcon24cd", how="left", validate="m:1")
    if member_join.filter(
            ~pl.col("member_id").eq_missing(pl.col("expected_member_id"))
            | (pl.col("member_status") != pl.col("expected_member_status"))).height:
        raise ValueError("Parliamentary relationship member differs from the current dimension")
    non_patient = links.filter(pl.col("relationship_basis") != "registered_patients")
    if non_patient.filter(pl.col("registered_patients").is_not_null()
                          | pl.col("share_of_practice_list").is_not_null()).height:
        raise ValueError("Non-GP site or operator relationship carries patient counts")
    patient_links = links.filter(pl.col("relationship_basis") == "registered_patients")
    expected = patients.filter(pl.col("pcon24cd") != "UNMAPPED").select(
        pl.col("practice_code").alias("org_code"), "pcon24cd", "patient_count"
    ).sort("org_code", "pcon24cd")
    actual = patient_links.select("org_code", "pcon24cd",
                                  pl.col("registered_patients").alias("patient_count")
                                  ).sort("org_code", "pcon24cd")
    if not expected.equals(actual):
        raise ValueError("MP join changed GP practice/PCON patient counts")
    if profile.filter(pl.col("organisation_type") != "gp_practice").filter(
            pl.col("registered_patients_total").is_not_null()
            | pl.col("served_constituencies_json").is_not_null()
            | pl.col("served_constituency_count").is_not_null()
            | pl.col("unmapped_patient_count").is_not_null()).height:
        raise ValueError("Non-GP parliamentary profile carries patient fields")
    expected_by_practice: dict[str, list[tuple[str, int]]] = defaultdict(list)
    source_totals: dict[str, int] = defaultdict(int)
    for row in patients.iter_rows(named=True):
        source_totals[row["practice_code"]] += row["patient_count"]
        if row["pcon24cd"] != "UNMAPPED":
            expected_by_practice[row["practice_code"]].append(
                (row["pcon24cd"], row["patient_count"]))
    for row in profile.filter(pl.col("organisation_type") == "gp_practice").iter_rows(named=True):
        code = row["org_code"]
        served = json.loads(row["served_constituencies_json"])
        actual_served = sorted((item["pcon24cd"], item["registered_patients"])
                               for item in served)
        if (actual_served != sorted(expected_by_practice[code])
                or row["served_constituency_count"] != len(actual_served)
                or row["registered_patients_total"] != source_totals.get(code)):
            raise ValueError(f"GP parliamentary profile does not reconcile for {code}")
    if brief["registered_patients_mapped"].sum() != expected["patient_count"].sum():
        raise ValueError("Parliamentary brief does not reconcile mapped patients")
    by_pcon = patient_links.group_by("pcon24cd").agg(
        pl.col("registered_patients").sum().alias("expected_patients"),
        pl.len().alias("expected_practices"),
    )
    checked_brief = brief.join(by_pcon, on="pcon24cd", how="left").with_columns(
        pl.col("expected_patients", "expected_practices").fill_null(0))
    if checked_brief.filter(
            (pl.col("registered_patients_mapped") != pl.col("expected_patients"))
            | (pl.col("serving_gp_practice_count") != pl.col("expected_practices"))).height:
        raise ValueError("Constituency brief does not reconcile GP patient links")
    return {
        "member_rows": members.height,
        "matched_pcon24_rows": members.height,
        "member_match_rate": 1.0,
        "unmatched_parliament_constituencies": [],
        "unmatched_pcon24_constituencies": [],
        "current_members": active.height,
        "vacant_constituencies": members.height - active.height,
        "member_source_snapshot_date": str(members["source_snapshot_date"][0]),
        "brief_rows": brief.height, "organisation_profile_rows": profile.height,
        "relationship_rows": links.height,
        "relationships_by_basis": dict(sorted(Counter(links["relationship_basis"]).items())),
        "mapped_patients_in_relationships": int(actual["patient_count"].sum() or 0),
        "mapped_patients_in_source_bridge": int(expected["patient_count"].sum() or 0),
        "patient_difference": 0,
    }


def write_parliamentary_outputs(processed: Path, members: pl.DataFrame,
                                brief: pl.DataFrame, profile: pl.DataFrame,
                                links: pl.DataFrame) -> None:
    for name, table in zip(PARLIAMENT_TABLES, (members, brief, profile, links), strict=True):
        table.write_parquet(processed / f"{name}.parquet")
        if name != "dim_pcon_member":
            table.write_csv(processed / f"{name}.csv")
    root = processed / "json"
    constituency_dir = root / "constituencies"
    organisation_dir = root / "organisations"
    constituency_dir.mkdir(parents=True)
    organisation_dir.mkdir(parents=True)
    by_pcon: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    by_org: dict[str, list] = defaultdict(list)
    for row in links.iter_rows(named=True):
        basis = row["relationship_basis"]
        by_pcon[row["pcon24cd"]][basis].append(row)
        by_org[row["org_code"]].append(row)
    for row in brief.iter_rows(named=True):
        code = row["pcon24cd"]
        detail = by_pcon[code]
        payload = {**row, "site_counts_by_type": json.loads(row["site_counts_by_type_json"]),
                   "site_organisations": sorted(detail["site_location"],
                                                key=lambda r: (r["organisation_type"], r["org_code"])),
                   "serving_gp_practices": sorted(detail["registered_patients"],
                                                  key=lambda r: (-r["registered_patients"],
                                                                 r["org_code"])),
                   "operating_relationships": sorted(detail["operating_relationship"],
                                                     key=lambda r: (r["parent_org_code"],
                                                                    r["org_code"]))}
        (constituency_dir / f"{code}.json").write_text(_json(payload) + "\n", encoding="utf-8")
    for row in profile.iter_rows(named=True):
        code = row["org_code"]
        payload = {**row, "served_constituencies": json.loads(row["served_constituencies_json"])
                   if row["served_constituencies_json"] is not None else None,
                   "relationships": by_org[code]}
        (organisation_dir / f"{code}.json").write_text(_json(payload) + "\n", encoding="utf-8")


def audit_parliamentary_outputs(processed: Path, manifest: dict) -> dict:
    tables = {name: pl.read_parquet(processed / f"{name}.parquet") for name in PARLIAMENT_TABLES}
    result = validate_parliamentary_tables(
        tables["dim_pcon_member"], tables["pcon_parliamentary_brief"],
        tables["organisation_parliamentary_profile"], tables["mp_nhs_relationship"],
        pl.read_parquet(processed / "pcon24.parquet"),
        pl.read_parquet(processed / "organisation_pcon_profile.parquet"),
        pl.read_parquet(processed / "gp_practice_patient_pcon.parquet"),
    )
    for name, table in tables.items():
        if table.height != manifest["row_counts"][f"{name}_rows"]:
            raise ValueError(f"{name} row count differs from build manifest")
    for kind, codes in (("constituencies", tables["dim_pcon_member"]["pcon24cd"]),
                        ("organisations", tables["organisation_parliamentary_profile"]["org_code"])):
        folder = processed / "json" / kind
        if {path.stem for path in folder.glob("*.json")} != set(codes):
            raise ValueError(f"Parliamentary {kind} JSON files do not cover the table")
    return result
