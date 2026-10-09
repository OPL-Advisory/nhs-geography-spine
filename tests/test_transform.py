import polars as pl
from nhs_geo_spine.transform import map_lsoa_to_pcon
from nhs_geo_spine.qa import reconcile_counts


def test_patient_mapping_reconciles():
    src = pl.DataFrame({
        "practice_code": ["A", "A", "A"],
        "LSOA21CD": ["E01000001", "E01000002", "E01099999"],
        "patient_count": [10, 20, 5],
    })
    lookup = pl.DataFrame({
        "LSOA21CD": ["E01000001", "E01000002"],
        "PCON24CD": ["E14000001", "E14000001"],
        "PCON24NM": ["Example", "Example"],
    })
    out = map_lsoa_to_pcon(src, lookup)
    reconcile_counts(src, out)
    assert out.select(pl.col("patient_count").sum()).item() == 35
    assert out.filter(pl.col("PCON24CD") == "UNMAPPED").select("patient_count").item() == 5
