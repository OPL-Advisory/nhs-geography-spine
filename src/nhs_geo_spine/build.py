"""Command-line entry point for a frozen, reproducible NHS geography build."""

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import polars as pl
import typer

from .ingest_gp_patients import read_gp_patients
from .ingest_ods import read_gp_practices
from .ingest_ons import read_lsoa_lookup
from .ingest_postcodes import read_pcon_names, write_postcodes
from .qa import audit_outputs, create_qa_report
from .sources import fetch_sources, load_config, verified_sources
from .transform import aggregate_gp_patients, map_sites

app = typer.Typer(no_args_is_help=True)
DEFAULT_CONFIG = Path("config/sources.yml")
DEFAULT_RAW = Path("data/raw")
DEFAULT_PROCESSED = Path("data/processed")


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _git_state() -> dict[str, str | bool | None]:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return {"git_sha": None, "git_dirty": None}
    return {"git_sha": sha, "git_dirty": dirty}


def _create_duckdb(processed: Path) -> None:
    database = processed / "nhs_geography.duckdb"
    temporary = processed / "nhs_geography.tmp.duckdb"
    temporary.unlink(missing_ok=True)
    tables = {
        "dim_geography_lsoa21": "lsoa21_pcon24.parquet",
        "dim_pcon24": "pcon24.parquet",
        "dim_postcode": "postcode_spine.parquet",
        "dim_nhs_organisation_site": "nhs_organisation_sites.parquet",
        "bridge_org_site_pcon": "nhs_org_to_pcon.parquet",
        "bridge_gp_practice_patient_pcon": "gp_practice_patient_pcon.parquet",
        "gp_patient_unmapped_lsoa": "gp_patient_unmapped_lsoa.parquet",
        "gp_practice_source_totals": "gp_practice_source_totals.parquet",
    }
    with duckdb.connect(str(temporary)) as connection:
        for name, filename in tables.items():
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet(?)", [str(processed / filename)])
        connection.execute("""
            CREATE VIEW pcon_nhs_organisations AS
            SELECT pcon24cd, pcon24nm, org_code, site_code, org_name, org_role,
                   postcode, mapping_method, mapping_quality
            FROM bridge_org_site_pcon
            WHERE pcon24cd IS NOT NULL AND status = 'ACTIVE'
        """)
        connection.execute("""
            CREATE VIEW pcon_gp_patient_links AS
            SELECT pcon24cd, pcon24nm, practice_code, patient_count,
                   patient_share AS share_of_practice_list,
                   patient_count::DOUBLE / SUM(patient_count) OVER (PARTITION BY pcon24cd)
                       AS share_of_constituency_registered_patients,
                   lsoa_source_period, mapping_method
            FROM bridge_gp_practice_patient_pcon
            WHERE pcon24cd <> 'UNMAPPED'
        """)
        connection.execute("""
            CREATE VIEW gp_practice_pcon_profile AS
            WITH totals AS (
                SELECT b.practice_code, MAX(s.pcon24cd) AS address_pcon24cd,
                       SUM(b.patient_count) AS practice_total,
                       COUNT(DISTINCT CASE WHEN b.pcon24cd <> 'UNMAPPED'
                           THEN b.pcon24cd END) AS constituencies_served,
                       SUM(CASE WHEN s.pcon24cd IS NOT NULL
                           AND b.pcon24cd <> 'UNMAPPED' AND b.pcon24cd <> s.pcon24cd
                           THEN b.patient_count ELSE 0 END) AS outside_address_patients
                FROM bridge_gp_practice_patient_pcon b
                LEFT JOIN bridge_org_site_pcon s ON s.org_code = b.practice_code
                GROUP BY b.practice_code
            )
            SELECT b.practice_code, b.pcon24cd, b.pcon24nm, b.patient_count,
                   b.patient_share, b.lsoa_source_period, b.mapping_method,
                   t.address_pcon24cd, t.constituencies_served,
                   CASE WHEN t.address_pcon24cd IS NULL THEN NULL
                        ELSE t.outside_address_patients END AS outside_address_patients,
                   CASE WHEN t.address_pcon24cd IS NULL THEN NULL
                        ELSE t.outside_address_patients::DOUBLE / NULLIF(t.practice_total, 0)
                   END AS outside_address_share
            FROM bridge_gp_practice_patient_pcon b
            JOIN totals t ON t.practice_code = b.practice_code
        """)
        connection.execute("CHECKPOINT")
    temporary.replace(database)


def build_pipeline(config_path: Path = DEFAULT_CONFIG, raw_dir: Path = DEFAULT_RAW,
                   processed_dir: Path = DEFAULT_PROCESSED, offline: bool = False) -> dict:
    """Build all canonical tables from verified raw bytes and return QA results."""
    config = load_config(config_path)
    if not offline:
        fetch_sources(config, raw_dir)
    ledger = verified_sources(config, raw_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    source = ledger["sources"]
    ons = read_lsoa_lookup(raw_dir / config["ons_lsoa21_pcon24"].filename,
                           config["ons_lsoa21_pcon24"].source_version)
    ons.write_parquet(processed_dir / "lsoa21_pcon24.parquet")
    postcode_spec = config["nhs_postcode_directory"]
    pcon = read_pcon_names(raw_dir / postcode_spec.filename, postcode_spec.pcon_member)
    pcon.write_parquet(processed_dir / "pcon24.parquet")
    if set(ons["pcon24cd_best_fit"].to_list()) - set(pcon["pcon24cd"].to_list()):
        raise ValueError("ONS LSOA lookup has a PCON24 code absent from the NHSPD code set")
    sites, ods_source_rows = read_gp_practices(
        raw_dir / config["ods_gp_practices"].filename,
        source["ods_gp_practices"]["source_date"], config["ods_gp_practices"].source_version,
    )
    sites.write_parquet(processed_dir / "nhs_organisation_sites.parquet")
    postcode_counts = write_postcodes(
        raw_dir / postcode_spec.filename, postcode_spec.data_member,
        processed_dir / "postcode_spine.parquet", pcon,
        postcode_spec.source_date, postcode_spec.source_version,
    )
    site_bridge = map_sites(sites, processed_dir / "postcode_spine.parquet", ons,
                            postcode_spec.source_version, config["ons_lsoa21_pcon24"].source_version)
    site_bridge.write_parquet(processed_dir / "nhs_org_to_pcon.parquet")
    site_bridge.write_csv(processed_dir / "nhs_org_to_pcon.csv")
    patient_spec = config["gp_registered_patients_lsoa"]
    patient_lsoa = read_gp_patients(raw_dir / patient_spec.filename,
                                    patient_spec.data_member, patient_spec.source_date)
    patient_bridge, unmapped = aggregate_gp_patients(patient_lsoa, ons, patient_spec.source_version)
    practice_source_totals = patient_lsoa.group_by("practice_code").agg(
        pl.col("patient_count").sum().alias("source_total")
    ).sort("practice_code")
    practice_source_totals.write_parquet(processed_dir / "gp_practice_source_totals.parquet")
    patient_bridge.write_parquet(processed_dir / "gp_practice_patient_pcon.parquet")
    patient_bridge.write_csv(processed_dir / "gp_practice_patient_pcon.csv")
    unmapped.write_parquet(processed_dir / "gp_patient_unmapped_lsoa.parquet")
    patient_total = int(patient_lsoa.select(pl.col("patient_count").sum()).item())
    report = create_qa_report(ons, sites, site_bridge, patient_bridge, unmapped,
                              patient_total, pcon, postcode_counts, practice_source_totals)
    _write_json(processed_dir / "qa_report.json", report)
    _create_duckdb(processed_dir)
    manifest = {
        "build_timestamp": datetime.now(UTC).isoformat(),
        "code": {"package_version": "0.1.0", **_git_state()},
        "sources": source,
        "geography_vintages": {"lsoa": "2021 England/Wales", "pcon": "July 2024",
                                "postcode": postcode_spec.source_version},
        "row_counts": {
            "ons_lsoa_source_rows": ons.height, "lsoa_lookup_rows": ons.height,
            "pcon24_code_rows": pcon.height, "ods_epraccur_source_rows": ods_source_rows,
            "ods_gp_practice_rows": sites.height, "postcode_directory": postcode_counts,
            "site_bridge_rows": site_bridge.height, "gp_patient_source_rows": patient_lsoa.height,
            "gp_patient_source_total": patient_total, "gp_patient_bridge_rows": patient_bridge.height,
            "gp_patient_bridge_total": int(patient_bridge.select(pl.col("patient_count").sum()).item()),
            "gp_patient_unmapped_lsoa_rows": unmapped.height,
            "gp_practice_source_totals_rows": practice_source_totals.height,
        },
        "qa_summary": {"status": report["status"],
                       "valid_active_england_gp_mapping_rate": report["site_mapping"][
                           "valid_active_england_gp_mapping_rate"],
                       "threshold_99_percent_met": report["site_mapping"]["threshold_99_percent_met"],
                       "patient_difference": report["patient_reconciliation"]["difference"]},
    }
    _write_json(processed_dir / "build_manifest.json", manifest)
    return report


@app.command()
def fetch(config: Path = typer.Option(DEFAULT_CONFIG), raw_dir: Path = typer.Option(DEFAULT_RAW),
          refresh: bool = typer.Option(False, help="Redownload all inputs, including nightly ODS.")) -> None:
    """Download or verify the pinned public sources and save SHA-256 provenance."""
    ledger = fetch_sources(load_config(config), raw_dir, refresh=refresh)
    for key, record in ledger["sources"].items():
        typer.echo(f"{key}: {record['source_date']} {record['sha256'][:12]}")


@app.command()
def build(config: Path = typer.Option(DEFAULT_CONFIG), raw_dir: Path = typer.Option(DEFAULT_RAW),
          processed_dir: Path = typer.Option(DEFAULT_PROCESSED),
          offline: bool = typer.Option(False, help="Require verified cached sources; never access the network.")) -> None:
    """Build Parquet/CSV bridges, DuckDB, manifest and QA report."""
    report = build_pipeline(config, raw_dir, processed_dir, offline)
    england_rate = report["site_mapping"]["valid_active_england_gp_mapping_rate"]
    rate_display = f"{england_rate:.2%}" if england_rate is not None else "unavailable"
    typer.echo(f"Build {report['status']}: {report['patient_reconciliation']['bridge_total']} patients; "
               f"England valid active GP postcode mapping rate: {rate_display}")


@app.command()
def qa(processed_dir: Path = typer.Option(DEFAULT_PROCESSED)) -> None:
    """Rerun hard validation against saved outputs without network access."""
    report = audit_outputs(processed_dir)
    _write_json(processed_dir / "qa_report.json", report)
    typer.echo(f"QA {report['status']}; patient difference: {report['patient_reconciliation']['difference']}")


@app.command()
def export(format: str = typer.Option(..., help="parquet, csv or duckdb"),
           processed_dir: Path = typer.Option(DEFAULT_PROCESSED),
           output_dir: Path | None = typer.Option(None)) -> None:
    """Copy the ready analytical bundle in the requested format."""
    files = {
        "parquet": ["lsoa21_pcon24.parquet", "postcode_spine.parquet", "pcon24.parquet",
                    "nhs_organisation_sites.parquet", "nhs_org_to_pcon.parquet",
                    "gp_practice_patient_pcon.parquet", "gp_patient_unmapped_lsoa.parquet",
                    "gp_practice_source_totals.parquet"],
        "csv": ["nhs_org_to_pcon.csv", "gp_practice_patient_pcon.csv"],
        "duckdb": ["nhs_geography.duckdb"],
    }
    if format not in files:
        raise typer.BadParameter("format must be parquet, csv or duckdb")
    target = output_dir or processed_dir
    target.mkdir(parents=True, exist_ok=True)
    for name in files[format]:
        source = processed_dir / name
        if not source.exists():
            raise FileNotFoundError(f"{source} is missing; run `nhs-geo build` first")
        destination = target / name
        if destination.resolve() != source.resolve():
            shutil.copy2(source, destination)
        typer.echo(str(destination))


if __name__ == "__main__":
    app()
