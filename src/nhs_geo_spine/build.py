"""Command-line entry point for a frozen, reproducible NHS geography build."""

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp

import duckdb
import polars as pl
import typer

from .ingest_gp_patients import read_gp_patients
from .ingest_ods import read_gp_practices
from .ingest_ons import read_lsoa_lookup
from .ingest_postcodes import read_pcon_names, write_postcodes
from .ingest_providers import expand_gp_schema, read_icb_codes, read_provider_reports
from .parliament import (
    MEMBER_SOURCE_KEY,
    PARLIAMENT_OUTPUTS,
    PARLIAMENT_TABLES,
    audit_parliamentary_outputs,
    make_parliamentary_tables,
    read_members,
    validate_parliamentary_tables,
    write_parliamentary_outputs,
)
from .qa import attach_provider_qa, audit_outputs, create_qa_report
from .sources import (
    PARLIAMENT_SOURCE_KEY,
    PROVIDER_SOURCE_KEYS,
    Source,
    fetch_sources,
    load_config,
    verified_sources,
)
from .transform import aggregate_gp_patients, map_sites

app = typer.Typer(no_args_is_help=True)
DEFAULT_CONFIG = Path("config/sources.yml")
DEFAULT_RAW = Path("data/raw")
DEFAULT_PROCESSED = Path("data/processed")
BUILD_OUTPUTS = (
    "lsoa21_pcon24.parquet", "pcon24.parquet", "nhs_organisation_sites.parquet",
    "postcode_spine.parquet", "nhs_org_to_pcon.parquet", "nhs_org_to_pcon.csv",
    "gp_practice_source_totals.parquet", "gp_practice_patient_pcon.parquet",
    "gp_practice_patient_pcon.csv", "gp_patient_unmapped_lsoa.parquet",
    "qa_report.json", "nhs_geography.duckdb",
    "icb26_codes.parquet", "all_nhs_organisation_sites.parquet",
    "all_nhs_org_to_pcon.parquet", "all_nhs_org_to_pcon.csv",
    "pcon_nhs_organisations.parquet", "pcon_nhs_organisations.csv",
    "pcon_provider_summary.parquet", "pcon_provider_summary.csv",
    "organisation_pcon_profile.parquet", "organisation_pcon_profile.csv",
)
OUTPUT_MARKER = ".nhs-geography-spine-output"
OUTPUT_MARKER_CONTENT = "nhs-geography-spine-output-v1\n"
_OWNED_FILES = set(BUILD_OUTPUTS) | set(PARLIAMENT_OUTPUTS) | {
    "build_manifest.json", ".gitkeep", OUTPUT_MARKER,
}


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


def _check_output_destination(config_path: Path, raw_dir: Path, processed_dir: Path) -> None:
    """Refuse a destructive replacement unless the destination is a dedicated bundle."""
    target = processed_dir.resolve()
    raw = raw_dir.resolve()
    protected = (Path.cwd().resolve(), config_path.resolve(), raw,
                 Path(__file__).resolve().parents[2])
    if (processed_dir.is_symlink() or any(path == target or path.is_relative_to(target)
                                          for path in protected)
            or target.is_relative_to(raw)):
        raise ValueError(f"Unsafe processed directory: {processed_dir}")
    if not processed_dir.exists():
        return
    if not processed_dir.is_dir():
        raise ValueError(f"Processed destination is not a directory: {processed_dir}")
    entries = list(processed_dir.iterdir())
    if not entries:
        return
    marker = processed_dir / OUTPUT_MARKER
    if marker.exists():
        if (marker.is_symlink() or not marker.is_file()
                or marker.read_text(encoding="utf-8") != OUTPUT_MARKER_CONTENT):
            raise ValueError(f"Invalid processed bundle ownership marker: {marker}")
    else:
        legacy_default = target == DEFAULT_PROCESSED.resolve()
        legacy_bundle = ((processed_dir / "build_manifest.json").is_file()
                         or {path.name for path in entries} == {".gitkeep"})
        if not legacy_default or not legacy_bundle:
            raise ValueError(f"Unowned processed directory contains files: {processed_dir}")
    for path in entries:
        if path.is_symlink() or path.name not in _OWNED_FILES | {"json"}:
            raise ValueError(f"Unowned file in processed directory: {path}")
        if path.name == "json":
            if not path.is_dir():
                raise ValueError(f"Unexpected processed JSON entry: {path}")
            for category, table, column in (
                ("constituencies", "dim_pcon_member", "pcon24cd"),
                ("organisations", "organisation_parliamentary_profile", "org_code"),
            ):
                folder = path / category
                if not folder.is_dir() or folder.is_symlink():
                    raise ValueError(f"Unexpected processed JSON folder: {folder}")
                allowed_codes = set(pl.read_parquet(processed_dir / f"{table}.parquet",
                                                    columns=[column])[column])
                for item in folder.iterdir():
                    if (item.is_symlink() or not item.is_file() or item.suffix != ".json"
                            or item.stem not in allowed_codes):
                        raise ValueError(f"Unowned file in processed directory: {item}")
            if {item.name for item in path.iterdir()} != {"constituencies", "organisations"}:
                raise ValueError(f"Unowned file in processed JSON directory: {path}")
        elif not path.is_file():
            raise ValueError(f"Unexpected processed output entry: {path}")


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
        "dim_icb26_code": "icb26_codes.parquet",
        "dim_all_nhs_organisation_site": "all_nhs_organisation_sites.parquet",
        "bridge_all_org_site_pcon": "all_nhs_org_to_pcon.parquet",
        "bridge_gp_practice_patient_pcon": "gp_practice_patient_pcon.parquet",
        "gp_patient_unmapped_lsoa": "gp_patient_unmapped_lsoa.parquet",
        "gp_practice_source_totals": "gp_practice_source_totals.parquet",
    }
    with duckdb.connect(str(temporary)) as connection:
        for name, filename in tables.items():
            connection.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet(?)", [str(processed / filename)])
        connection.execute("""
            CREATE VIEW pcon_nhs_organisations AS
            SELECT s.pcon24cd, s.pcon24nm, s.org_code, s.site_code, s.org_name,
                   s.org_role, s.postcode, s.mapping_method, s.mapping_quality,
                   s.organisation_type, s.primary_role_id, s.non_primary_role_ids,
                   s.parent_org_code, p.org_name AS parent_org_name,
                   p.organisation_type AS parent_organisation_type,
                   p.status AS parent_status,
                   s.relationship_type, s.relationship_start_date,
                   s.relationship_end_date, p.pcon24cd AS parent_address_pcon24cd,
                   s.open_date, s.close_date, s.status_basis, s.role_evidence,
                   s.source_report, s.source_version, s.source_snapshot_date,
                   'site_postcode' AS geography_basis
            FROM bridge_all_org_site_pcon s
            LEFT JOIN bridge_all_org_site_pcon p ON p.org_code = s.parent_org_code
            WHERE s.pcon24cd IS NOT NULL AND s.status = 'ACTIVE'
        """)
        connection.execute("""
            CREATE VIEW pcon_provider_summary AS
            SELECT pcon24cd, pcon24nm, organisation_type,
                   COUNT(DISTINCT org_code) AS organisation_codes,
                   'site_postcode' AS geography_basis
            FROM pcon_nhs_organisations
            GROUP BY pcon24cd, pcon24nm, organisation_type
        """)
        connection.execute("""
            CREATE VIEW organisation_pcon_profile AS
            SELECT s.org_code, s.org_name, s.organisation_type, s.org_role,
                   s.status, s.status_basis, s.postcode, s.country_code,
                   s.pcon24cd AS address_pcon24cd,
                   s.pcon24nm AS address_pcon24nm, s.mapping_method,
                   s.mapping_quality, s.unmapped_reason, s.parent_org_code,
                   p.org_name AS parent_org_name,
                   p.organisation_type AS parent_organisation_type,
                   p.status AS parent_status,
                   p.pcon24cd AS parent_address_pcon24cd,
                   s.relationship_type, s.relationship_start_date,
                   s.relationship_end_date, s.open_date, s.close_date,
                   s.role_evidence, s.source_report, s.source_version,
                   s.source_snapshot_date,
                   'site_postcode' AS geography_basis
            FROM bridge_all_org_site_pcon s
            LEFT JOIN bridge_all_org_site_pcon p ON p.org_code = s.parent_org_code
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
        for name, order in (
            ("pcon_nhs_organisations", "pcon24cd, organisation_type, org_code"),
            ("pcon_provider_summary", "pcon24cd, organisation_type"),
            ("organisation_pcon_profile", "org_code"),
        ):
            result = connection.execute(f"SELECT * FROM {name} ORDER BY {order}").pl()
            result.write_parquet(processed / f"{name}.parquet")
            result.write_csv(processed / f"{name}.csv")
    temporary.replace(database)


def build_pipeline(config_path: Path = DEFAULT_CONFIG, raw_dir: Path = DEFAULT_RAW,
                   processed_dir: Path = DEFAULT_PROCESSED, offline: bool = False) -> dict:
    """Build all canonical tables from verified raw bytes and return QA results."""
    _check_output_destination(config_path, raw_dir, processed_dir)
    config = load_config(config_path)
    if not offline:
        fetch_sources(config, raw_dir)
    ledger = verified_sources(config, raw_dir)
    code_state = _git_state()
    processed_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(mkdtemp(prefix=f".{processed_dir.name}-build-", dir=processed_dir.parent))
    backup = stage.with_name(stage.name + "-previous")
    published = False
    try:
        report = _build_bundle(config, ledger, raw_dir, stage, code_state)
        outputs = BUILD_OUTPUTS + (PARLIAMENT_OUTPUTS if MEMBER_SOURCE_KEY in config else ())
        missing = [name for name in (*outputs, "build_manifest.json") if not (stage / name).is_file()]
        if missing:
            raise RuntimeError(f"Staged build is missing outputs: {missing}")
        if MEMBER_SOURCE_KEY in config:
            for category in ("constituencies", "organisations"):
                if not (stage / "json" / category).is_dir():
                    raise RuntimeError(f"Staged parliamentary JSON {category} is missing")
            audit_parliamentary_outputs(stage, json.loads(
                (stage / "build_manifest.json").read_text(encoding="utf-8")))
        (stage / ".gitkeep").write_text("\n", encoding="utf-8")
        (stage / OUTPUT_MARKER).write_text(OUTPUT_MARKER_CONTENT, encoding="utf-8")
        _check_output_destination(config_path, raw_dir, processed_dir)
        if backup.exists() or backup.is_symlink():
            raise RuntimeError(f"Build backup path already exists: {backup}")
        if processed_dir.exists():
            processed_dir.replace(backup)
        try:
            stage.replace(processed_dir)
        except OSError:
            if backup.exists():
                backup.replace(processed_dir)
            raise
        published = True
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if published and backup.exists():
            shutil.rmtree(backup)
    return report


def _build_bundle(config: dict[str, Source], ledger: dict, raw_dir: Path,
                  processed_dir: Path,
                  code_state: dict[str, str | bool | None]) -> dict:
    """Produce a complete bundle in an unpublished staging directory."""
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
    if all(key in config for key in PROVIDER_SOURCE_KEYS):
        icb_codes = read_icb_codes(raw_dir / config["ons_icb26_codes"].filename)
        provider_sites, provider_source_rows = read_provider_reports(config, ledger, raw_dir, icb_codes)
        all_sites = pl.concat([expand_gp_schema(sites), provider_sites], how="vertical").sort("org_code")
    else:
        icb_codes = pl.DataFrame(schema={"icb26cd": pl.String, "org_code": pl.String,
                                         "icb26nm": pl.String})
        provider_source_rows = {}
        all_sites = expand_gp_schema(sites)
    icb_codes.write_parquet(processed_dir / "icb26_codes.parquet")
    all_sites.write_parquet(processed_dir / "all_nhs_organisation_sites.parquet")
    all_bridge = map_sites(all_sites, processed_dir / "postcode_spine.parquet", ons,
                           postcode_spec.source_version, config["ons_lsoa21_pcon24"].source_version)
    all_bridge = all_bridge.join(all_sites.select(
        "org_code", "organisation_type", "primary_role_id", "non_primary_role_ids",
        "parent_org_code", "operating_org_code", "relationship_type",
        "relationship_start_date", "relationship_end_date", "role_evidence", "status_basis",
        "source_report", "source_version", "open_date", "close_date",
    ), on="org_code", validate="1:1").sort("org_code")
    all_bridge.write_parquet(processed_dir / "all_nhs_org_to_pcon.parquet")
    all_bridge.write_csv(processed_dir / "all_nhs_org_to_pcon.csv")
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
    attach_provider_qa(report, all_sites, all_bridge, pcon)
    _create_duckdb(processed_dir)
    if MEMBER_SOURCE_KEY in config:
        member_spec = config[MEMBER_SOURCE_KEY]
        members = read_members(raw_dir / member_spec.filename, pcon, source[MEMBER_SOURCE_KEY])
        org_profile = pl.read_parquet(processed_dir / "organisation_pcon_profile.parquet")
        brief, profile, links = make_parliamentary_tables(members, org_profile, patient_bridge)
        report["parliamentary"] = validate_parliamentary_tables(
            members, brief, profile, links, pcon, org_profile, patient_bridge)
        write_parliamentary_outputs(processed_dir, members, brief, profile, links)
        with duckdb.connect(str(processed_dir / "nhs_geography.duckdb")) as connection:
            for name in PARLIAMENT_TABLES:
                connection.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet(?)",
                                   [str(processed_dir / f"{name}.parquet")])
            connection.execute("CHECKPOINT")
    _write_json(processed_dir / "qa_report.json", report)
    manifest = {
        "build_timestamp": datetime.now(UTC).isoformat(),
        "code": {"package_version": "0.3.0" if MEMBER_SOURCE_KEY in config else "0.2.0",
                 **code_state},
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
            "ons_icb26_code_rows": icb_codes.height,
            "provider_reports": provider_source_rows,
            "all_ods_organisation_rows": all_sites.height,
            "all_site_bridge_rows": all_bridge.height,
            "pcon_nhs_organisations_rows": pl.read_parquet(
                processed_dir / "pcon_nhs_organisations.parquet").height,
            "pcon_provider_summary_rows": pl.read_parquet(
                processed_dir / "pcon_provider_summary.parquet").height,
            "organisation_pcon_profile_rows": pl.read_parquet(
                processed_dir / "organisation_pcon_profile.parquet").height,
        },
        "qa_summary": {"status": report["status"],
                       "valid_active_england_gp_mapping_rate": report["site_mapping"][
                           "valid_active_england_gp_mapping_rate"],
                       "threshold_99_percent_met": report["site_mapping"]["threshold_99_percent_met"],
                       "patient_difference": report["patient_reconciliation"]["difference"],
                       "provider_active_unmapped_rows": report["provider_coverage"][
                           "active_unmapped_rows"],
                       "provider_unresolved_active_re6_relationships": report[
                           "provider_coverage"]["unresolved_active_re6_relationships"],
                       "provider_missing_active_re6_operators": report[
                           "provider_coverage"]["missing_active_re6_operator_rows"],
                       "active_child_parent_not_active": len(report["provider_coverage"][
                           "active_child_parent_not_active"])},
    }
    if MEMBER_SOURCE_KEY in config:
        manifest["row_counts"].update({
            f"{name}_rows": pl.read_parquet(processed_dir / f"{name}.parquet").height
            for name in PARLIAMENT_TABLES
        })
        manifest["qa_summary"]["parliamentary"] = report["parliamentary"]
    _write_json(processed_dir / "build_manifest.json", manifest)
    return report


@app.command()
def fetch(config: Path = typer.Option(DEFAULT_CONFIG), raw_dir: Path = typer.Option(DEFAULT_RAW),
          refresh: bool = typer.Option(False, help="Redownload all inputs, including nightly ODS."),
          refresh_member: bool = typer.Option(False, help="Refresh only the current UK Parliament member snapshot.")) -> None:
    """Download or verify the pinned public sources and save SHA-256 provenance."""
    selected = load_config(config)
    if refresh_member:
        if PARLIAMENT_SOURCE_KEY not in selected:
            raise typer.BadParameter("the config has no UK Parliament member source")
        selected = {PARLIAMENT_SOURCE_KEY: selected[PARLIAMENT_SOURCE_KEY]}
    ledger = fetch_sources(selected, raw_dir, refresh=refresh or refresh_member)
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
                    "gp_practice_source_totals.parquet", "icb26_codes.parquet",
                    "all_nhs_organisation_sites.parquet", "all_nhs_org_to_pcon.parquet",
                    "pcon_nhs_organisations.parquet", "pcon_provider_summary.parquet",
                    "organisation_pcon_profile.parquet"],
        "csv": ["nhs_org_to_pcon.csv", "gp_practice_patient_pcon.csv",
                "all_nhs_org_to_pcon.csv", "pcon_nhs_organisations.csv",
                "pcon_provider_summary.csv", "organisation_pcon_profile.csv"],
        "duckdb": ["nhs_geography.duckdb"],
    }
    if format not in files:
        raise typer.BadParameter("format must be parquet, csv or duckdb")
    if (processed_dir / "dim_pcon_member.parquet").exists():
        files["parquet"].extend(f"{name}.parquet" for name in PARLIAMENT_TABLES)
        files["csv"].extend(f"{name}.csv" for name in PARLIAMENT_TABLES
                            if name != "dim_pcon_member")
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


def _show_json(path: Path, code: str, kind: str, as_json: bool) -> None:
    if not code.isascii() or not code.isalnum():
        raise typer.BadParameter(f"Invalid {kind} code {code}")
    if not path.is_file():
        raise typer.BadParameter(f"Unknown {kind} code {code}; build the parliamentary layer first")
    value = json.loads(path.read_text(encoding="utf-8"))
    if as_json:
        typer.echo(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
        return
    if kind == "constituency":
        typer.echo(f"{value['pcon24nm']} ({code})")
        typer.echo(f"MP: {value['member_name'] or 'Vacant'}"
                   + (f" ({value['party_name']})" if value['party_name'] else ""))
        typer.echo(f"Site locations: {value['site_organisation_count']} "
                   f"{value['site_counts_by_type']}")
        for row in value["site_organisations"][:10]:
            typer.echo(f"  site_location: {row['org_code']} {row['org_name']} "
                       f"[{row['organisation_type']}]")
        if len(value["site_organisations"]) > 10:
            typer.echo(f"  +{len(value['site_organisations']) - 10} more sites; use --json for all")
        typer.echo(f"GP practices serving residents: {value['serving_gp_practice_count']}; "
                   f"mapped patients: {value['registered_patients_mapped']}")
        for row in value["serving_gp_practices"][:10]:
            typer.echo(f"  registered_patients: {row['org_code']} {row['org_name'] or ''} "
                       f"{row['registered_patients']} "
                       f"({row['share_of_practice_list']:.1%} of practice list)")
        if len(value["serving_gp_practices"]) > 10:
            typer.echo(f"  +{len(value['serving_gp_practices']) - 10} more practices; "
                       "use --json for all")
        for row in value["operating_relationships"][:10]:
            typer.echo(f"  operating_relationship: {row['org_code']} -> {row['parent_org_code']}")
        if len(value["operating_relationships"]) > 10:
            typer.echo(f"  +{len(value['operating_relationships']) - 10} more operators; "
                       "use --json for all")
    else:
        typer.echo(f"{value['org_name']} ({code}) [{value['organisation_type']}]")
        typer.echo(f"site_location address PCON: {value['address_pcon24cd'] or 'Unmapped'}; "
                   f"MP: {value['address_member_name'] or value['address_member_status'] or 'Unavailable'}")
        if value["parent_org_code"]:
            if value["parent_relationship_current_at_snapshot"]:
                typer.echo(f"operating_relationship RE6 operator: {value['parent_org_code']} "
                           f"{value['parent_org_name'] or ''}; parent address PCON: "
                           f"{value['parent_current_address_pcon24cd'] or 'Unmapped'}")
            else:
                temporal = value["parent_relationship_temporal_status"] or "undated"
                status = (temporal if value["status"] == "ACTIVE"
                          else f"{temporal}; organisation {value['status'].lower()}")
                typer.echo(f"Source RE6 operator ({status}; not a current parliamentary link): "
                           f"{value['parent_org_code']} {value['parent_org_name'] or ''}; "
                           f"parent address PCON: {value['parent_address_pcon24cd'] or 'Unmapped'}")
        if value["organisation_type"] == "gp_practice":
            typer.echo(f"Registered patients: {value['registered_patients_total']}; "
                       f"unmapped: {value['unmapped_patient_count']}")
            for row in sorted(value["served_constituencies"],
                              key=lambda r: (-r["registered_patients"], r["pcon24cd"]))[:10]:
                typer.echo(f"  registered_patients: {row['pcon24cd']} {row['pcon24nm']} "
                           f"{row['registered_patients']} "
                           f"({row['share_of_practice_list']:.1%}); "
                           f"MP: {row['member_name'] or 'Vacant'}")
            if len(value["served_constituencies"]) > 10:
                typer.echo(f"  +{len(value['served_constituencies']) - 10} more constituencies; "
                           "use --json for all")


@app.command()
def constituency(pcon24cd: str, processed_dir: Path = typer.Option(DEFAULT_PROCESSED),
                 json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON.")) -> None:
    """Inspect current MP, physical sites and GP patient links for a PCON24 code."""
    code = pcon24cd.upper()
    _show_json(processed_dir / "json" / "constituencies" / f"{code}.json",
               code, "constituency", json_output)


@app.command()
def organisation(ods_code: str, processed_dir: Path = typer.Option(DEFAULT_PROCESSED),
                 json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON.")) -> None:
    """Inspect an ODS code's address MP, operator and GP patient links."""
    code = ods_code.upper()
    _show_json(processed_dir / "json" / "organisations" / f"{code}.json",
               code, "organisation", json_output)


if __name__ == "__main__":
    app()
