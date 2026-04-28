"""HSE incident trend analysis, rolling average, and overdue action detection."""
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta


def compute_hse_report(df, schemas_dir: str, as_of: date = None) -> dict:
    """Process an HSE DataFrame and return a structured report dict.

    Returns:
        {
            "this_week_counts": {"MTC": int, "FAC": int, "NM": int},
            "rolling_avg":      {"MTC": float, "FAC": float, "NM": float},
            "overdue_actions":  [{"hub", "incident_type", "description", "due_date", "days_overdue"}],
            "total_this_week":  int,
            "overdue_count":    int,
            "weeks_analyzed":   int,
        }
    """
    with open(os.path.join(schemas_dir, "hse.json")) as f:
        schema = json.load(f)

    incident_types  = schema.get("incident_types", ["MTC", "FAC", "NM"])
    grace_days      = schema.get("overdue_grace_days", 7)

    if as_of is None:
        as_of = date.today()

    # Parse all rows
    parsed = []
    for row in df:
        try:
            incident_date = datetime.strptime(row["date"].strip(), "%Y-%m-%d").date()
            due_date      = datetime.strptime(row["due_date"].strip(), "%Y-%m-%d").date()
            week_date     = datetime.strptime(row["week_date"].strip(), "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue

        parsed.append({
            "date":              incident_date,
            "hub":               row.get("hub", ""),
            "incident_type":     row.get("incident_type", ""),
            "description":       row.get("description", ""),
            "corrective_action": row.get("corrective_action", ""),
            "due_date":          due_date,
            "status":            row.get("status", ""),
            "week_date":         week_date,
        })

    if not parsed:
        return {
            "this_week_counts": {t: 0 for t in incident_types},
            "rolling_avg":      {t: 0.0 for t in incident_types},
            "overdue_actions":  [],
            "total_this_week":  0,
            "overdue_count":    0,
            "weeks_analyzed":   0,
        }

    # Identify all distinct week_dates and find the most recent
    all_weeks = sorted(set(r["week_date"] for r in parsed))
    latest_week = all_weeks[-1]

    # This week's counts
    this_week_rows = [r for r in parsed if r["week_date"] == latest_week]
    this_week_counts = defaultdict(int)
    for r in this_week_rows:
        t = r["incident_type"]
        if t in incident_types:
            this_week_counts[t] += 1

    # Rolling average across ALL recorded weeks
    weekly_counts: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in parsed:
        t = r["incident_type"]
        if t in incident_types:
            weekly_counts[r["week_date"]][t] += 1

    weeks_analyzed = len(all_weeks)
    rolling_avg = {}
    for t in incident_types:
        total = sum(weekly_counts[w].get(t, 0) for w in all_weeks)
        rolling_avg[t] = round(total / weeks_analyzed, 2) if weeks_analyzed else 0.0

    # Overdue actions: status=='Overdue' OR (status=='Open' AND due_date + grace_days < as_of)
    overdue_actions = []
    for r in parsed:
        if r["status"] == "Closed":
            continue
        deadline_with_grace = r["due_date"] + timedelta(days=grace_days)
        is_overdue = (r["status"] == "Overdue") or (
            r["status"] == "Open" and deadline_with_grace < as_of
        )
        if is_overdue:
            days_overdue = (as_of - r["due_date"]).days
            overdue_actions.append({
                "hub":           r["hub"],
                "incident_type": r["incident_type"],
                "description":   r["description"],
                "due_date":      r["due_date"].strftime("%Y-%m-%d"),
                "days_overdue":  max(days_overdue, 1),
            })

    return {
        "this_week_counts": dict(this_week_counts),
        "rolling_avg":      rolling_avg,
        "overdue_actions":  sorted(overdue_actions, key=lambda x: -x["days_overdue"]),
        "total_this_week":  sum(this_week_counts.values()),
        "overdue_count":    len(overdue_actions),
        "weeks_analyzed":   weeks_analyzed,
    }
