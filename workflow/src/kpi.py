"""KPI delta calculation, threshold flagging, and top-miss ranking."""
import json
import os


def compute_kpi_report(df, schemas_dir: str) -> dict:
    """Process a KPI DataFrame and return a structured report dict.

    Returns:
        {
            "rows": [
                {
                    "hub": str,
                    "metric": str,
                    "this_week": float,
                    "last_week": float,
                    "target": float,
                    "delta": float,          # absolute change
                    "delta_pct": float,      # % change vs last_week
                    "rag": str,              # RED / AMBER / GREEN vs target
                    "flagged": bool,         # True if WoW change exceeds threshold
                }
            ],
            "top_misses": [...],     # top 5 flagged rows sorted by |delta_pct| desc
            "flagged_count": int,
        }
    """
    with open(os.path.join(schemas_dir, "kpi.json")) as f:
        schema = json.load(f)

    threshold = schema.get("flag_threshold_pct", 5.0)
    higher_is_better = set(schema.get("metrics_higher_is_better", []))
    lower_is_better  = set(schema.get("metrics_lower_is_better", []))

    rows = []
    for row in df:
        try:
            this_week = float(row["this_week"])
            last_week = float(row["last_week"])
            target    = float(row["target"])
        except (ValueError, KeyError):
            continue

        delta     = this_week - last_week
        delta_pct = (delta / last_week * 100) if last_week != 0 else 0.0
        metric    = row["metric"]

        # Flagging: did the metric move in a bad direction beyond threshold?
        if metric in higher_is_better:
            flagged = delta_pct < -threshold
        elif metric in lower_is_better:
            flagged = delta_pct > threshold
        else:
            # Unknown directionality: flag any absolute change > threshold
            flagged = abs(delta_pct) > threshold

        # RAG vs target
        if metric in higher_is_better:
            gap_to_target_pct = (this_week - target) / target * 100 if target else 0
            if this_week >= target:
                rag = "GREEN"
            elif gap_to_target_pct >= -3:
                rag = "AMBER"
            else:
                rag = "RED"
        elif metric in lower_is_better:
            if this_week <= target:
                rag = "GREEN"
            elif (this_week - target) / target * 100 <= 3:
                rag = "AMBER"
            else:
                rag = "RED"
        else:
            rag = "AMBER"

        rows.append({
            "hub":       row["hub"],
            "metric":    metric,
            "this_week": this_week,
            "last_week": last_week,
            "target":    target,
            "delta":     round(delta, 2),
            "delta_pct": round(delta_pct, 1),
            "rag":       rag,
            "flagged":   flagged,
            "week_date": row.get("week_date", ""),
        })

    flagged_rows = [r for r in rows if r["flagged"]]
    top_misses   = sorted(flagged_rows, key=lambda r: abs(r["delta_pct"]), reverse=True)[:5]

    return {
        "rows":          rows,
        "top_misses":    top_misses,
        "flagged_count": len(flagged_rows),
    }
