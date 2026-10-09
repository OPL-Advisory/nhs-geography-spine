import polars as pl

from nhs_geo_spine.qa import reconcile_counts
from nhs_geo_spine.transform import map_lsoa_to_pcon


def test_patient_mapping_reconciles():
    src = pl.DataFrame({
        "practice_code": ["A", "A", "A"],
        "lsoa21cd": ["E01000001", "E01000002", "E01099999"],
        "patient_count": [10, 20, 5],
        "lsoa_source_period": ["2026-07-01"] * 3,
    })
    lookup = pl.DataFrame({
        "lsoa21cd": ["E01000001", "E01000002"],
        "pcon24cd_best_fit": ["E14000001", "E14000001"],
        "pcon24nm_best_fit": ["Example", "Example"],
    })
    out = map_lsoa_to_pcon(src, lookup)
    reconcile_counts(src, out)
    assert out.select(pl.col("patient_count").sum()).item() == 35
    assert out.filter(pl.col("pcon24cd") == "UNMAPPED").select("patient_count").item() == 5
