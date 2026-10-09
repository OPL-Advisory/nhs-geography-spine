"""Official DSE provider/commissioner reports and the ONS ICB code set."""

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from .ingest_ods import _date
from .normalise import normalise_postcode
from .sources import Source


@dataclass(frozen=True)
class Report:
    key: str
    name: str
    organisation_type: str
    primary_role_id: str
    is_site: bool = False
    has_operator: bool = False
    relationship_dates: bool = False


REPORTS = (
    Report("ods_gp_branches", "ebranchs", "gp_branch_surgery", "RO96", True, True, True),
    Report("ods_nhs_trusts", "etr", "nhs_trust", "RO197"),
    Report("ods_nhs_trust_sites", "ets", "nhs_trust_site", "RO198", True, True),
    Report("ods_other", "eother", "integrated_care_board", "RO261"),
    Report("ods_sub_icb_locations", "eccg", "sub_icb_unit", "RO98"),
    Report("ods_sub_icb_sites", "eccgsite", "sub_icb_location_site", "RO99", True, True, True),
)


def read_icb_codes(path: Path) -> pl.DataFrame:
    """Read the ONS April 2026 ICB register; ICB26CDH is the ODS code."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"ICB26CD", "ICB26CDH", "ICB26NM"}.issubset(reader.fieldnames):
            raise ValueError("ONS ICB26 code list schema changed")
        rows = [{"icb26cd": row["ICB26CD"].strip(),
                 "org_code": row["ICB26CDH"].strip().upper(),
                 "icb26nm": row["ICB26NM"].strip()} for row in reader]
    if not rows or any(not r["icb26cd"] or not r["org_code"] or not r["icb26nm"]
                       or not re.fullmatch(r"[A-Z0-9]{3,12}", r["org_code"]) for r in rows):
        raise ValueError("ONS ICB26 code list contains invalid or no codes")
    table = pl.DataFrame(rows).sort("org_code")
    if table["org_code"].n_unique() != table.height or table["icb26cd"].n_unique() != table.height:
        raise ValueError("ONS ICB26 code list contains duplicate codes")
    return table


def _sub_icb_type(non_primary_roles: str) -> str:
    roles = set(non_primary_roles.split("|"))
    if "RO319" in roles:
        return "sub_icb_location"
    if "RO327" in roles:
        return "sub_icb_reporting_entity"
    if "RO218" in roles:
        return "commissioning_hub"
    return "former_clinical_commissioning_group"


def _read_report(path: Path, report: Report, snapshot_date: str, source_version: str,
                 icb_codes: set[str]) -> tuple[list[dict], int]:
    snapshot = date.fromisoformat(snapshot_date)
    rows: list[dict] = []
    source_rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for line, record in enumerate(csv.reader(stream), start=1):
            source_rows += 1
            if len(record) != 27:
                raise ValueError(f"ODS {report.name} schema changed: row {line} has {len(record)} columns, expected 27")
            code = record[0].strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{3,12}", code) or not record[1].strip():
                raise ValueError(f"ODS {report.name} invalid code/name at row {line}")
            if report.name == "eother" and code not in icb_codes:
                continue
            opened, closed = _date(record[10]), _date(record[11])
            if opened is None or closed is not None and closed < opened:
                raise ValueError(f"ODS {report.name} invalid legal dates at row {line}")
            relationship_start = _date(record[15]) if report.relationship_dates else None
            relationship_end = _date(record[16]) if report.relationship_dates else None
            if relationship_start and relationship_end and relationship_end < relationship_start:
                raise ValueError(f"ODS {report.name} invalid RE6 dates at row {line}")
            parent = (record[14].strip().upper() or None) if report.has_operator else None
            if report.has_operator and not parent:
                raise ValueError(f"ODS {report.name} missing RE6 operator at row {line}")
            non_primary = ("RO318" if report.name == "eother" else
                           record[13].strip() or None)
            org_type = (_sub_icb_type(non_primary or "") if report.name == "eccg"
                        else report.organisation_type)
            postcode, compact = normalise_postcode(record[9])
            rows.append({
                "org_code": code,
                "site_code": code if report.is_site else None,
                "org_name": record[1].strip(),
                "org_role": report.primary_role_id + ("|" + non_primary if non_primary else ""),
                "parent_org_code": parent,
                "operating_org_code": parent,
                "commissioner_code": None,
                "status": "INACTIVE" if closed and closed <= snapshot else "ACTIVE",
                "address_line_1": record[4].strip() or None,
                "town": record[7].strip() or None,
                "postcode_raw": record[9],
                "postcode": postcode,
                "postcode_compact": compact,
                "open_date": opened,
                "close_date": closed,
                "source_snapshot_date": snapshot,
                "source": "NHS England ODS DSE " + report.name,
                "source_version": source_version,
                "source_report": report.name,
                "organisation_type": org_type,
                "primary_role_id": report.primary_role_id,
                "non_primary_role_ids": non_primary,
                "relationship_type": "RE6" if parent else None,
                "relationship_start_date": relationship_start,
                "relationship_end_date": relationship_end,
                "role_evidence": ("ONS ICB26CDH code list; DSE eother lacks role columns"
                                  if report.name == "eother" else "ODS DSE report specification"),
                "status_basis": "DSE legal end date",
            })
    if not rows:
        raise ValueError(f"ODS {report.name} contains no selected records")
    if report.name == "eother" and {row["org_code"] for row in rows} != icb_codes:
        missing = sorted(icb_codes - {row["org_code"] for row in rows})
        raise ValueError(f"ODS eother missing ONS ICB26 codes: {missing}")
    return rows, source_rows


def read_provider_reports(config: dict[str, Source], ledger: dict, raw_dir: Path,
                          icb_codes: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, dict[str, int]]]:
    """Read all v0.2 reports, keeping every row from the single-role reports."""
    codes = set(icb_codes["org_code"].to_list())
    rows: list[dict] = []
    counts: dict[str, dict[str, int]] = {}
    for report in REPORTS:
        spec = config[report.key]
        selected, source_rows = _read_report(
            raw_dir / spec.filename, report, ledger["sources"][report.key]["source_date"],
            spec.source_version, codes,
        )
        rows.extend(selected)
        counts[report.name] = {"source_rows": source_rows, "selected_rows": len(selected)}
    table = pl.DataFrame(rows, infer_schema_length=None, schema_overrides={
        "site_code": pl.String, "parent_org_code": pl.String,
        "operating_org_code": pl.String, "commissioner_code": pl.String,
        "address_line_1": pl.String, "town": pl.String,
        "postcode": pl.String, "postcode_compact": pl.String,
        "open_date": pl.Date, "close_date": pl.Date, "source_snapshot_date": pl.Date,
        "non_primary_role_ids": pl.String, "relationship_type": pl.String,
        "relationship_start_date": pl.Date, "relationship_end_date": pl.Date,
    })
    if table["org_code"].n_unique() != table.height:
        raise ValueError("ODS provider reports contain duplicate organisation codes")
    return table.sort("org_code"), counts


def expand_gp_schema(practices: pl.DataFrame) -> pl.DataFrame:
    """Add v0.2 metadata without changing the published v0.1 GP table."""
    return practices.with_columns(
        pl.lit("epraccur").alias("source_report"),
        pl.lit("gp_practice").alias("organisation_type"),
        pl.lit("RO177").alias("primary_role_id"),
        pl.col("org_role").alias("non_primary_role_ids"),
        pl.lit(None, dtype=pl.String).alias("relationship_type"),
        pl.lit(None, dtype=pl.Date).alias("relationship_start_date"),
        pl.lit(None, dtype=pl.Date).alias("relationship_end_date"),
        pl.lit("ODS DSE epraccur role column").alias("role_evidence"),
        pl.lit("DSE status column").alias("status_basis"),
    )
