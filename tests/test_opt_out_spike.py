"""The research adapter keeps official practice grain and absent values explicit."""

import csv
from pathlib import Path

import pytest

from spikes.opt_out import analyse


def _csv(path: Path, records: list[tuple[str, str, str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Date", "Practice Code", "Practice Name", "Measure Names",
                         "Measure Values", "Icb Code", "Region Name1"])
        for code, name, measure, value in records:
            writer.writerow(["08/05/2026", code, name, measure, value, "Q001", "Example"])
        writer.writerow(["08/12/2026", "A81001", "Practice One", "Opt-out", "99", "Q001", "Example"])


def _rows() -> list[tuple[str, str, str, str]]:
    return [
        ("A81001", "Practice One", "Opt-out", "10"),
        ("A81001", "Practice One", "List size", "100"),
        ("A81001", "Practice One", "Opt-out rate", "10%"),
        ("A81001", "Practice One", "Deceased", "7"),
        ("A81002", "Practice Two", "Opt-out", "*"),
        ("A81002", "Practice Two", "List size", ""),
        ("A81002", "Practice Two", "Opt-out rate", "*"),
        ("R00001", "Wrong Type", "Opt-out", "0"),
        ("R00001", "Wrong Type", "List size", "200"),
        ("R00001", "Wrong Type", "Opt-out rate", "0%"),
        ("ZZ9999", "Unknown Code", "Opt-out", "5"),
        ("ZZ9999", "Unknown Code", "List size", "100"),
        ("ZZ9999", "Unknown Code", "Opt-out rate", "5%"),
    ]


def _profiles() -> dict[str, dict]:
    return {
        "A81001": {"organisation_type": "gp_practice", "address_pcon24cd": "E14001063"},
        "A81002": {"organisation_type": "gp_practice", "address_pcon24cd": None},
        "R00001": {"organisation_type": "nhs_trust", "address_pcon24cd": "E14001064"},
    }


def test_practice_join_reasons_and_missing_not_zero(tmp_path: Path) -> None:
    source = tmp_path / "official-shaped.csv"
    _csv(source, _rows())
    result = analyse(source, _profiles(), date="2026-08-05")
    assert result["source_rows_on_date"] == 13
    assert result["practice_codes"] == 4
    assert result["join_reasons"] == {
        "code_is_not_gp_practice": 1, "matched_gp_address_only": 1,
        "matched_gp_address_unmapped": 1, "not_in_spine_snapshot": 1,
    }
    assert result["measure_states"]["opt_out_count:available"] == 3
    assert result["measure_states"]["opt_out_count:suppressed"] == 1
    assert result["measure_states"]["list_size:unknown"] == 1
    by_code = {row["practice_code"]: row for row in result["sample"]}
    assert by_code["R00001"]["opt_out_count"] == 0
    assert by_code["A81002"]["opt_out_count"] is None
    assert by_code["A81001"]["official_list_size"] == 100
    assert by_code["A81001"]["spine_address_pcon24cd_context_only"] == "E14001063"
    assert "constituency_opt_out" not in str(result)
    assert "source_sha256" in result


@pytest.mark.parametrize("extra,reason", [
    ([("A81001", "Practice One", "Opt-out", "11")], "Duplicate"),
    ([("A81001", "Practice One", "Opt-out rate", "80%")], "Duplicate"),
])
def test_duplicate_official_grain_fails(tmp_path: Path, extra: list, reason: str) -> None:
    source = tmp_path / "duplicate.csv"
    _csv(source, _rows() + extra)
    with pytest.raises(ValueError, match=reason):
        analyse(source, _profiles(), date="2026-08-05")


def test_conflicting_rate_or_count_and_wrong_date_fail(tmp_path: Path) -> None:
    source = tmp_path / "inconsistent.csv"
    rows = [(code, name, measure, "80%" if code == "A81001" and measure == "Opt-out rate"
             else value) for code, name, measure, value in _rows()]
    _csv(source, rows)
    with pytest.raises(ValueError, match="disagrees"):
        analyse(source, _profiles(), date="2026-08-05")
    with pytest.raises(ValueError, match="No NHS opt-out rows"):
        analyse(source, _profiles(), date="2026-07-01")
    rows = [(code, name, measure, "110" if code == "A81001" and measure == "Opt-out"
             else value) for code, name, measure, value in _rows()]
    _csv(source, rows)
    with pytest.raises(ValueError, match="exceeds"):
        analyse(source, _profiles(), date="2026-08-05")
