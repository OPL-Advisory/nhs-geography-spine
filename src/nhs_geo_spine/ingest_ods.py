"""ODS Data Search and Export epraccur report adapter (27 headerless columns)."""

import csv
import re
from datetime import date, datetime
from pathlib import Path

import polars as pl

from .normalise import normalise_postcode


def _date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"ODS report has invalid YYYYMMDD date {value!r}") from exc


def read_gp_practices(path: Path, snapshot_date: str, source_version: str) -> tuple[pl.DataFrame, int]:
    """Read RO76 GP practices only; retain source role/status and raw postcode."""
    try:
        snapshot = date.fromisoformat(snapshot_date)
    except ValueError as exc:
        raise ValueError(f"ODS invalid source snapshot date {snapshot_date!r}") from exc
    rows: list[dict] = []
    source_rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for line, record in enumerate(csv.reader(stream), start=1):
            source_rows += 1
            if len(record) != 27:
                raise ValueError(f"ODS epraccur schema changed: row {line} has {len(record)} columns, expected 27")
            roles = record[25].split("|")
            if "RO76" not in roles:
                continue
            code = record[0].strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{6,12}", code):
                raise ValueError(f"ODS epraccur invalid GP practice code at row {line}: {code!r}")
            postcode, compact = normalise_postcode(record[9])
            rows.append({
                "org_code": code,
                "site_code": None,
                "org_name": record[1].strip(),
                "org_role": record[25].strip(),
                "parent_org_code": None,
                "operating_org_code": record[23].strip() or None,
                "commissioner_code": record[14].strip() or None,
                "status": record[12].strip().upper(),
                "address_line_1": record[4].strip() or None,
                "town": record[7].strip() or None,
                "postcode_raw": record[9],
                "postcode": postcode,
                "postcode_compact": compact,
                "open_date": _date(record[10]),
                "close_date": _date(record[11]),
                "source_snapshot_date": snapshot,
                "source": "NHS England ODS DSE epraccur",
                "source_version": source_version,
            })
    if not rows:
        raise ValueError("ODS epraccur contains no RO76 GP practice records")
    table = pl.DataFrame(rows, infer_schema_length=None, schema_overrides={
        "site_code": pl.String, "parent_org_code": pl.String,
        "operating_org_code": pl.String, "commissioner_code": pl.String,
        "address_line_1": pl.String, "town": pl.String,
        "postcode": pl.String, "postcode_compact": pl.String,
        "open_date": pl.Date, "close_date": pl.Date, "source_snapshot_date": pl.Date,
    })
    if table.get_column("org_code").n_unique() != table.height:
        raise ValueError("ODS epraccur has duplicate GP practice codes")
    return table.sort("org_code"), source_rows
