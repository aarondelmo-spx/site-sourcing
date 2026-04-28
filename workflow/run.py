#!/usr/bin/env python3
"""
SPX Weekly Ops & Compliance Report — Cowork Automation Entry Point

Usage:
    python run.py                          # Uses today's date, default folders
    python run.py --date 2026-04-14        # Override report date
    python run.py --inputs ./inputs --outputs ./outputs

Input folder must contain:
    kpi_tracker.csv
    compliance_tracker.csv
    hse_tracker.csv
"""
import argparse
import os
import sys
import json
from datetime import date, datetime

# ── Allow running from anywhere ───────────────────────────────────────────────
WORKFLOW_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKFLOW_DIR)

from src.ingest import load_csv, validate_schema, IngestError
from src.kpi import compute_kpi_report
from src.compliance import compute_compliance_report
from src.hse import compute_hse_report
from src.report import generate_report

SCHEMAS_DIR = os.path.join(WORKFLOW_DIR, "schemas")

REQUIRED_FILES = {
    "kpi":        "kpi_tracker.csv",
    "compliance": "compliance_tracker.csv",
    "hse":        "hse_tracker.csv",
}


def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {level:7s}  {msg}", flush=True)


def run(inputs_dir: str, outputs_dir: str, as_of: date) -> str:
    """Execute the full pipeline. Returns the output file path."""
    log(f"Starting SPX Weekly Report — week of {as_of}")
    log(f"Inputs:  {inputs_dir}")
    log(f"Outputs: {outputs_dir}")

    os.makedirs(outputs_dir, exist_ok=True)

    # ── 1. Ingest + validate ──────────────────────────────────────────────────
    data = {}
    validation_errors = []

    for schema_name, filename in REQUIRED_FILES.items():
        path = os.path.join(inputs_dir, filename)
        log(f"Loading {filename} ...")

        if not os.path.exists(path):
            log(f"MISSING: {path}", "ERROR")
            validation_errors.append(f"Missing input file: {filename}")
            continue

        try:
            df = load_csv(path)
        except Exception as e:
            log(f"Failed to read {filename}: {e}", "ERROR")
            validation_errors.append(str(e))
            continue

        result = validate_schema(df, schema_name, SCHEMAS_DIR)
        if not result["valid"]:
            log(f"Schema errors in {filename}:", "ERROR")
            for err in result["errors"]:
                log(f"  {err}", "ERROR")
            validation_errors.extend(result["errors"])
        else:
            log(f"  ✓ {result['row_count']} rows valid")
            data[schema_name] = df

    if validation_errors:
        error_log_path = os.path.join(outputs_dir, f"errors_{as_of}.log")
        with open(error_log_path, "w") as f:
            f.write("\n".join(validation_errors))
        log(f"Validation failed. Error log: {error_log_path}", "ERROR")
        raise RuntimeError(f"Validation failed with {len(validation_errors)} error(s). See {error_log_path}")

    # ── 2. Process ────────────────────────────────────────────────────────────
    log("Processing KPI data ...")
    kpi_report = compute_kpi_report(data["kpi"], SCHEMAS_DIR)
    log(f"  ✓ {len(kpi_report['rows'])} metrics · {kpi_report['flagged_count']} flagged")

    log("Processing compliance data ...")
    comp_report = compute_compliance_report(data["compliance"], SCHEMAS_DIR, as_of=as_of)
    log(f"  ✓ {comp_report['total_permits']} permits · RED={comp_report['red_count']} AMBER={comp_report['amber_count']}")

    log("Processing HSE data ...")
    hse_report = compute_hse_report(data["hse"], SCHEMAS_DIR, as_of=as_of)
    log(f"  ✓ {hse_report['total_this_week']} incidents this week · {hse_report['overdue_count']} overdue actions")

    # ── 3. Generate report ────────────────────────────────────────────────────
    date_str    = as_of.strftime("%Y-%m-%d")
    output_path = os.path.join(outputs_dir, f"SPX_Weekly_OpsCompliance_{date_str}.docx")
    log(f"Generating report → {output_path} ...")
    generate_report(
        kpi_report=kpi_report,
        compliance_report=comp_report,
        hse_report=hse_report,
        output_path=output_path,
        as_of=as_of,
    )
    log(f"  ✓ Report saved: {output_path}")

    # ── 4. Write metadata sidecar ─────────────────────────────────────────────
    meta = {
        "generated_at":    datetime.now().isoformat(),
        "report_date":     date_str,
        "kpi_rows":        len(kpi_report["rows"]),
        "kpi_flagged":     kpi_report["flagged_count"],
        "permits_total":   comp_report["total_permits"],
        "permits_red":     comp_report["red_count"],
        "permits_amber":   comp_report["amber_count"],
        "incidents_week":  hse_report["total_this_week"],
        "overdue_actions": hse_report["overdue_count"],
        "output_file":     os.path.basename(output_path),
    }
    meta_path = output_path.replace(".docx", "_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    log(f"  ✓ Metadata: {meta_path}")
    log("Pipeline complete.")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="SPX Weekly Ops & Compliance Report Generator")
    parser.add_argument("--date",    default=None,
                        help="Report date YYYY-MM-DD (default: today)")
    parser.add_argument("--inputs",  default=os.path.join(WORKFLOW_DIR, "inputs"),
                        help="Folder containing input CSVs")
    parser.add_argument("--outputs", default=os.path.join(WORKFLOW_DIR, "outputs"),
                        help="Folder for generated reports")
    args = parser.parse_args()

    if args.date:
        try:
            as_of = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"ERROR: Invalid date format '{args.date}'. Use YYYY-MM-DD.")
            sys.exit(1)
    else:
        as_of = date.today()

    try:
        output = run(args.inputs, args.outputs, as_of)
        print(f"\nReport ready: {output}")
    except RuntimeError as e:
        print(f"\nFailed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
