"""Offline, practice-only National Data Opt-Out join prototype.

This deliberately emits no constituency opt-out totals or rates. It accepts a
local copy of NHS England's geography CSV; downloading and publishing are separate
decisions. Run ``python -m spikes.opt_out --help`` for the fixture command.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import polars as pl

REQUIRED = {"Date", "Practice Code", "Practice Name", "Measure Names", "Measure Values"}
MEASURES = {"opt-out": "opt_out_count", "opt-out (does not include deceased)": "opt_out_count",
            "list size": "list_size", "opt-out rate": "opt_out_rate"}
MISSING = {"", "null", "none", "n/a", "na", "unknown"}
SUPPRESSED = {"*", "..", "suppressed", "-"}


def _date(raw: str) -> str:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Invalid NHS opt-out date: {raw!r}")


def _count(raw: str) -> tuple[str, int | None]:
    value = raw.strip().lower()
    if value in MISSING:
        return "unknown", None
    if value in SUPPRESSED:
        return "suppressed", None
    try:
        number = int(value.replace(",", ""))
    except ValueError as error:
        raise ValueError(f"Invalid NHS opt-out count: {raw!r}") from error
    if number < 0:
        raise ValueError(f"Negative NHS opt-out count: {raw!r}")
    return "available", number


def _rate(raw: str, count: int | None, list_size: int | None) -> tuple[str, float | None]:
    value = raw.strip().lower()
    if value in MISSING:
        return "unknown", None
    if value in SUPPRESSED:
        return "suppressed", None
    explicit_percent = value.endswith("%")
    try:
        numeric = float(value.rstrip("%").replace(",", ""))
    except ValueError as error:
        raise ValueError(f"Invalid NHS opt-out rate: {raw!r}") from error
    if not math.isfinite(numeric):
        raise ValueError(f"Non-finite NHS opt-out rate: {raw!r}")
    if numeric < 0:
        raise ValueError(f"Negative NHS opt-out rate: {raw!r}")
    expected = count / list_size if count is not None and list_size else None
    if explicit_percent:
        rate = numeric / 100
    elif expected is not None:
        candidates = [numeric, numeric / 100]
        rate = min(candidates, key=lambda candidate: abs(candidate - expected))
    else:
        return "unknown_unit", None
    return "available", rate


def load_spine_profiles(path: Path) -> dict[str, dict]:
    """Load only ODS identity/type/address fields from the accepted v0.3 profile."""
    frame = pl.read_parquet(path, columns=["org_code", "organisation_type",
                                          "address_pcon24cd", "status"])
    if frame["org_code"].n_unique() != frame.height:
        raise ValueError("Duplicate ODS code in spine profile")
    return {row["org_code"]: row for row in frame.to_dicts()}


def analyse(csv_path: Path, profiles: dict[str, dict], *, date: str,
            source_url: str = "https://digital.nhs.uk/dashboards/national-data-opt-out-open-data",
            source_version: str | None = None, retrieved_at: str | None = None,
            list_size_vintage: str | None = None,
            sample_limit: int = 20) -> dict:
    """Join one official practice/date grain by exact ODS code, retaining absences."""
    selected = _date(date)
    if retrieved_at is not None:
        timestamp = datetime.fromisoformat(retrieved_at)
        if timestamp.tzinfo is None:
            raise ValueError("Source retrieval timestamp must include a timezone")
    hasher = hashlib.sha256()
    with csv_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    rows: dict[str, dict[str, str]] = defaultdict(dict)
    names: dict[str, str] = {}
    selected_rows = 0
    total_rows = 0
    source_dates: dict[str, str] = {}
    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = REQUIRED - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Missing NHS geography CSV columns: {sorted(missing)}")
        for line, row in enumerate(reader, 2):
            total_rows += 1
            raw_date = row["Date"]
            if raw_date not in source_dates:
                source_dates[raw_date] = _date(raw_date)
            if source_dates[raw_date] != selected:
                continue
            selected_rows += 1
            code = row["Practice Code"].strip().upper()
            if not code:
                raise ValueError(f"Missing practice code on line {line}")
            name = row["Practice Name"].strip()
            if code in names and names[code] != name:
                raise ValueError(f"Conflicting practice name for {code}")
            names[code] = name
            measure = MEASURES.get(row["Measure Names"].strip().casefold())
            if measure is None:
                if row["Measure Names"].strip().casefold() == "deceased":
                    continue  # A separate source measure, never added to opt-out count.
                raise ValueError(f"Unexpected NHS geography measure on line {line}")
            if measure in rows[code]:
                raise ValueError(f"Duplicate {measure} for {code} on {selected}")
            rows[code][measure] = row["Measure Values"]
    if not selected_rows:
        raise ValueError(f"No NHS opt-out rows on {selected}")

    output = []
    joins: Counter[str] = Counter()
    states: Counter[str] = Counter()
    anomalies: Counter[str] = Counter()
    for code in sorted(names):
        measures = rows[code]
        count_state, count = _count(measures.get("opt_out_count", ""))
        size_state, size = _count(measures.get("list_size", ""))
        rate_state, rate = _rate(measures.get("opt_out_rate", ""), count, size)
        row_anomalies = []
        if count is not None and size is not None and count > size:
            row_anomalies.append("count_exceeds_source_list_size")
        if rate is not None and rate > 1:
            row_anomalies.append("source_rate_above_100_percent")
        if rate is not None and count is not None and size and abs(rate - count / size) > 0.002:
            row_anomalies.append("source_rate_disagrees_with_count_over_source_list_size")
        anomalies.update(row_anomalies)
        profile = profiles.get(code)
        if profile is None:
            join = "not_in_spine_snapshot"
        elif profile["organisation_type"] != "gp_practice":
            join = "code_is_not_gp_practice"
        elif profile["address_pcon24cd"] is None:
            join = "matched_gp_address_unmapped"
        else:
            join = "matched_gp_address_only"
        joins[join] += 1
        for field, state in (("opt_out_count", count_state), ("list_size", size_state),
                             ("opt_out_rate", rate_state)):
            states[f"{field}:{state}"] += 1
        output.append({
            "practice_code": code, "practice_name": names[code], "date": selected,
            "opt_out_count": count, "opt_out_count_state": count_state,
            "official_list_size": size, "official_list_size_state": size_state,
            "opt_out_rate": rate, "opt_out_rate_state": rate_state,
            "data_quality_flags": row_anomalies,
            "join_reason": join,
            "spine_address_pcon24cd_context_only": profile["address_pcon24cd"]
            if profile and profile["organisation_type"] == "gp_practice" else None,
        })
    return {
        "prototype_status": "offline_research_only_not_for_publication",
        "source_url": source_url, "source_file_name": csv_path.name,
        "source_sha256": digest, "source_version": source_version,
        "source_retrieved_at": retrieved_at,
        "source_provenance_caveats": [
            caveat for missing, caveat in (
                (source_version is None, "Original export version was not supplied."),
                (retrieved_at is None, "Original retrieval timestamp was not supplied; local file timestamps are not proof of retrieval time."),
                (list_size_vintage is None, "Practice list-size vintage was not verified for this source file."),
            ) if missing
        ],
        "source_date_range": {"first": min(source_dates.values()),
                              "last": max(source_dates.values()),
                              "distinct_dates": len(set(source_dates.values()))},
        "source_total_rows": total_rows, "date": selected,
        "grain": "one GP practice ODS code at one official opt-out date",
        "list_size_vintage": list_size_vintage,
        "rate_caveat": "Source rates use the source list size; verify its vintage before population-rate claims. Anomalous values are retained, not treated as choice probabilities.",
        "source_rows_on_date": selected_rows, "practice_codes": len(output),
        "join_reasons": dict(sorted(joins.items())),
        "measure_states": dict(sorted(states.items())),
        "data_quality_anomalies": dict(sorted(anomalies.items())),
        "sample": output[:sample_limit],
        "constraint": "No constituency opt-out counts or rates are emitted; address PCON is context only.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geography-csv", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, required=True,
                        help="v0.3 organisation_parliamentary_profile.parquet")
    parser.add_argument("--date", required=True, help="One explicit official snapshot date")
    parser.add_argument("--source-url", default="https://digital.nhs.uk/dashboards/national-data-opt-out-open-data")
    parser.add_argument("--source-version", help="Original export version, if recorded")
    parser.add_argument("--retrieved-at", help="Original retrieval timestamp with timezone, if recorded")
    parser.add_argument("--list-size-vintage", help="Verified source list-size vintage, if known")
    parser.add_argument("--output", type=Path, default=Path("data/opt-out-spike/practice-sample.json"))
    args = parser.parse_args()
    result = analyse(args.geography_csv, load_spine_profiles(args.profiles), date=args.date,
                     source_url=args.source_url, source_version=args.source_version,
                     retrieved_at=args.retrieved_at, list_size_vintage=args.list_size_vintage)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
                           encoding="utf-8")
    print(f"{result['practice_codes']} official practice codes; join reasons: {result['join_reasons']}")


if __name__ == "__main__":
    main()
