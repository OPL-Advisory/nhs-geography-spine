"""ONS NHS Postcode Directory full CSV archive adapter.

The headerless column positions are from the August 2026 NHSPD User Guide,
Annex A. This adapter deliberately fails if the record layout changes.
"""

import csv
import io
import re
import zipfile
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from .normalise import normalise_postcode

POSTCODE_SCHEMA = pa.schema([
    ("postcode_raw", pa.string()), ("postcode", pa.string()),
    ("postcode_compact", pa.string()), ("active", pa.bool_()),
    ("termination_yyyymm", pa.string()), ("country_code", pa.string()),
    ("oa_code", pa.string()), ("lsoa_code", pa.string()),
    ("msoa_code", pa.string()), ("lad_code", pa.string()),
    ("pcon_code", pa.string()), ("pcon_name", pa.string()),
    ("easting", pa.int64()), ("northing", pa.int64()),
    ("source_snapshot_date", pa.string()), ("source", pa.string()),
    ("source_version", pa.string()),
])


def read_pcon_names(archive_path: Path, member: str) -> pl.DataFrame:
    """Read the archive's 2024 UK constituency code/name table."""
    with zipfile.ZipFile(archive_path) as archive:
        if member not in archive.namelist():
            raise ValueError(f"NHSPD archive missing constituency names member {member}")
        with archive.open(member) as stream:
            reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
            if not reader.fieldnames or not {"PCON24CD", "PCON24NM"}.issubset(reader.fieldnames):
                raise ValueError("NHSPD constituency names schema changed")
            rows = [{"pcon24cd": r["PCON24CD"], "pcon24nm": r["PCON24NM"],
                     "pcon24nmw": r.get("PCON24NMW") or None} for r in reader]
    table = pl.DataFrame(rows, infer_schema_length=None)
    if table.is_empty() or table.get_column("pcon24cd").n_unique() != table.height:
        raise ValueError("NHSPD constituency code set is empty or duplicated")
    bad = table.filter(~pl.col("pcon24cd").str.contains(r"^(E140|W070|S140|N050)[0-9]{5}$"))
    if bad.height:
        raise ValueError(f"NHSPD names contain invalid PCON24 code: {bad['pcon24cd'][0]}")
    return table.sort("pcon24cd")


def _none(value: str) -> str | None:
    return value.strip() or None


def _int(value: str) -> int | None:
    return int(value) if value else None


def write_postcodes(
    archive_path: Path, member: str, output_path: Path, pcon_names: pl.DataFrame,
    snapshot_date: str, source_version: str, batch_size: int = 50_000,
) -> dict[str, int]:
    """Stream all NHSPD rows into a typed Parquet postcode dimension."""
    names = dict(zip(pcon_names["pcon24cd"], pcon_names["pcon24nm"], strict=True))
    seen: set[str] = set()
    rows: list[dict] = []
    count = invalid = active = pseudo_pcon = 0
    temporary = output_path.with_suffix(".tmp.parquet")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive, pq.ParquetWriter(
            temporary, POSTCODE_SCHEMA, compression="zstd"
        ) as writer:
            if member not in archive.namelist():
                raise ValueError(f"NHSPD archive missing data member {member}")
            with archive.open(member) as stream:
                reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
                for line, record in enumerate(reader, start=1):
                    if len(record) != 49:
                        raise ValueError(f"NHSPD schema changed: row {line} has {len(record)} columns, expected 49")
                    count += 1
                    display, compact = normalise_postcode(record[1])
                    if compact is None:
                        invalid += 1
                    elif compact in seen:
                        raise ValueError(f"Duplicate canonical postcode in NHSPD: {compact}")
                    else:
                        seen.add(compact)
                    code = _none(record[33])
                    if code and code not in names:
                        if re.fullmatch(r"[LM]99999999", code):
                            pseudo_pcon += 1
                            code = None
                        else:
                            raise ValueError(f"NHSPD PCON code outside July 2024 set at row {line}: {code}")
                    is_active = not bool(record[3])
                    active += int(is_active)
                    country = _none(record[12])
                    if country not in {None, "E92000001", "W92000004", "S92000003",
                                       "N92000002", "L93000001", "M83000003"}:
                        raise ValueError(f"NHSPD invalid country code at row {line}: {country}")
                    if country in {"E92000001", "W92000004"}:
                        prefix = "E" if country == "E92000001" else "W"
                        for index, pattern, label in (
                            (46, rf"^{prefix}00[0-9]{{6}}$", "OA21"),
                            (47, rf"^{prefix}010[0-9]{{5}}$", "LSOA21"),
                            (48, rf"^{prefix}020[0-9]{{5}}$", "MSOA21"),
                        ):
                            value = _none(record[index])
                            if value and not re.fullmatch(pattern, value):
                                raise ValueError(f"NHSPD invalid {label} code at row {line}: {value}")
                    rows.append({
                        "postcode_raw": record[1],
                        "postcode": display if display is not None else record[1].strip(),
                        "postcode_compact": compact,
                        "active": is_active,
                        "termination_yyyymm": _none(record[3]),
                        "country_code": country,
                        "oa_code": _none(record[46]),
                        "lsoa_code": _none(record[47]),
                        "msoa_code": _none(record[48]),
                        "lad_code": _none(record[8]),
                        "pcon_code": code,
                        "pcon_name": names.get(code),
                        "easting": _int(record[36]),
                        "northing": _int(record[37]),
                        "source_snapshot_date": snapshot_date,
                        "source": "ONS NHS Postcode Directory",
                        "source_version": source_version,
                    })
                    if len(rows) >= batch_size:
                        writer.write_table(pa.Table.from_pylist(rows, schema=POSTCODE_SCHEMA))
                        rows.clear()
                if rows:
                    writer.write_table(pa.Table.from_pylist(rows, schema=POSTCODE_SCHEMA))
        if count == 0:
            raise ValueError("NHSPD data member is empty")
        temporary.replace(output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"postcode_source_rows": count, "postcode_rows": count,
            "postcode_active_rows": active, "postcode_invalid_format_rows": invalid,
            "postcode_pseudo_pcon_rows": pseudo_pcon}
