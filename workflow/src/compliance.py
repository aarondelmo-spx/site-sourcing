"""Compliance permit expiry calculation, RAG bucketing, and action list generation."""
import json
import os
from datetime import date, datetime


def compute_compliance_report(df, schemas_dir: str, as_of: date = None) -> dict:
    """Process a compliance DataFrame and return a structured report dict.

    Returns:
        {
            "rows": [
                {
                    "hub": str, "site_code": str, "permit_type": str,
                    "permit_number": str, "expiry_date": date str,
                    "renewal_status": str, "assigned_to": str,
                    "days_to_expiry": int,
                    "rag": str,   # RED / AMBER / GREEN
                }
            ],
            "action_list": [...],   # RED + AMBER rows sorted by days_to_expiry asc
            "red_count": int,
            "amber_count": int,
            "green_count": int,
            "total_permits": int,
        }
    """
    with open(os.path.join(schemas_dir, "compliance.json")) as f:
        schema = json.load(f)

    thresholds = schema.get("rag_thresholds", {"red_days": 30, "amber_days": 60})
    red_days   = thresholds["red_days"]
    amber_days = thresholds["amber_days"]

    if as_of is None:
        as_of = date.today()

    rows = []
    for row in df:
        expiry_str = row.get("expiry_date", "").strip()
        try:
            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        days_to_expiry = (expiry_date - as_of).days

        if days_to_expiry <= red_days:
            rag = "RED"
        elif days_to_expiry <= amber_days:
            rag = "AMBER"
        else:
            rag = "GREEN"

        rows.append({
            "hub":            row.get("hub", ""),
            "site_code":      row.get("site_code", ""),
            "permit_type":    row.get("permit_type", ""),
            "permit_number":  row.get("permit_number", ""),
            "expiry_date":    expiry_str,
            "renewal_status": row.get("renewal_status", ""),
            "assigned_to":    row.get("assigned_to", ""),
            "days_to_expiry": days_to_expiry,
            "rag":            rag,
        })

    red_rows   = [r for r in rows if r["rag"] == "RED"]
    amber_rows = [r for r in rows if r["rag"] == "AMBER"]
    green_rows = [r for r in rows if r["rag"] == "GREEN"]

    # Action list: RED + AMBER sorted by urgency (lowest days first)
    action_list = sorted(red_rows + amber_rows, key=lambda r: r["days_to_expiry"])

    return {
        "rows":          rows,
        "action_list":   action_list,
        "red_count":     len(red_rows),
        "amber_count":   len(amber_rows),
        "green_count":   len(green_rows),
        "total_permits": len(rows),
    }
