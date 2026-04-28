"""
SPX Site Sourcing Dashboard — Phase 1

Streamlit dashboard for the Philippine logistics site sourcing CRM.

Features:
- PEZA CSV staleness banner (if data/peza_zones.csv is >90 days old)
- Spec summary sidebar
- "Run Scraper" button → spawns subprocess, polls status.json every 2s
- "Re-score" button → re-scores without re-scraping
- Ranked candidates table with score breakdown
- Filter: region, min score, status
- Incomplete listings listed separately
- "Reset scraper" button shown after crash

Run:
    streamlit run streamlit_app.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import List, Optional

import streamlit as st

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sourcing.models import ScoredListing, load_spec
from sourcing.providers.csv_providers import CsvPezaProvider
from sourcing.scorer.engine import ScoringEngine
from sourcing.storage import (
    load_scored,
    load_status,
    reset_status,
    save_status,
)

# ── Config ────────────────────────────────────────────────────────────────────
SPEC_PATH = os.path.join(ROOT, "spec.yaml")
DATA_DIR = os.path.join(ROOT, "data")
SCRAPER_CMD = [sys.executable, "-m", "sourcing.scrapers.orchestrator", "--spec", SPEC_PATH]
POLL_INTERVAL_S = 2


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_pid_alive(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def peza_staleness_check() -> Optional[str]:
    """Return warning string if PEZA CSV is stale, else None."""
    peza_path = os.path.join(DATA_DIR, "peza_zones.csv")
    if not os.path.exists(peza_path):
        return "⚠️ **PEZA zones data missing** — PEZA scoring is disabled. Add `data/peza_zones.csv` to enable."
    try:
        provider = CsvPezaProvider(peza_path)
        if provider.is_stale():
            days = provider.days_since_update()
            return (
                f"⚠️ **PEZA zones CSV is {days} days old** (threshold: 90 days). "
                "Consider refreshing `data/peza_zones.csv` from peza.gov.ph."
            )
    except Exception:
        pass
    return None


def score_breakdown_bar(breakdown: dict, score: float) -> str:
    """Build a compact score breakdown string for display."""
    parts = []
    for field, val in breakdown.items():
        if val > 0:
            parts.append(f"{field.replace('_', ' ')}: {val:.0f}")
    return " | ".join(parts) if parts else "-"


def render_listings_table(listings: List[ScoredListing], title: str, color: str = "#1a73e8") -> None:
    if not listings:
        return

    st.markdown(f"### {title} ({len(listings)})")

    rows = []
    for l in listings:
        dup_note = f" ⚠️ dup of {l.possible_duplicate_of}" if l.possible_duplicate_of else ""
        row = {
            "Score": f"{l.score:.0f}",
            "Title": l.listing.title[:60] or "(no title)",
            "Region": l.listing.region or "?",
            "sqft": f"{l.listing.sqft:,.0f}" if l.listing.sqft else "—",
            "Docks": str(l.listing.dock_doors) if l.listing.dock_doors is not None else "—",
            "Height (m)": f"{l.listing.clear_height_m:.1f}" if l.listing.clear_height_m else "—",
            "Flood": (l.enriched.flood_risk or "?").upper(),
            "PEZA km": f"{l.enriched.peza_zone_km:.1f}" if l.enriched.peza_zone_km else "—",
            "Price (₱)": f"{l.listing.price_php:,.0f}" if l.listing.price_php else "—",
            "Source": l.source,
            "Flags": dup_note,
            "Link": l.url,
        }
        rows.append(row)

    import pandas as pd
    df = pd.DataFrame(rows)

    # Clickable links
    st.dataframe(
        df,
        column_config={
            "Link": st.column_config.LinkColumn("Link"),
            "Score": st.column_config.NumberColumn("Score", format="%s / 100"),
        },
        use_container_width=True,
        hide_index=True,
    )


# ── Page setup ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SPX Site Sourcing",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🏭 SPX Site Sourcing — Candidate Dashboard")
st.caption("Philippines last-mile hub, fulfillment warehouse & sorting center sourcing")

# ── PEZA staleness banner ─────────────────────────────────────────────────────
peza_warn = peza_staleness_check()
if peza_warn:
    st.warning(peza_warn)

# ── Load spec ─────────────────────────────────────────────────────────────────
try:
    spec = load_spec(SPEC_PATH)
except Exception as e:
    st.error(f"**spec.yaml error:** {e}")
    st.stop()

# ── Sidebar — spec summary ────────────────────────────────────────────────────
with st.sidebar:
    st.header("📋 Active Spec")
    st.write(f"**Size:** {spec.min_sqft:,.0f} – {spec.max_sqft:,.0f} sqft")
    st.write(f"**Dock doors ≥** {spec.dock_doors_min}")
    st.write(f"**Clear height ≥** {spec.clear_height_m_min} m")
    st.write(f"**Regions:** {', '.join(spec.regions)}")
    if spec.corridor_access:
        st.write(f"**Corridors:** {', '.join(spec.corridor_access)}")
    if spec.peza_zone_within_km:
        st.write(f"**PEZA ≤** {spec.peza_zone_within_km} km")
    st.write(f"**Max flood:** {spec.max_flood_risk}")
    st.divider()
    st.caption("Edit `spec.yaml` to change requirements.")

    st.header("⚙️ Weights")
    w = spec.weights
    weight_data = {
        "Field": ["sqft", "dock_doors", "clear_height_m", "region",
                  "corridor_access", "peza_zone", "max_flood_risk"],
        "Weight": [w.sqft, w.dock_doors, w.clear_height_m, w.region,
                   w.corridor_access, w.peza_zone, w.max_flood_risk],
    }
    import pandas as pd
    st.dataframe(pd.DataFrame(weight_data), hide_index=True, use_container_width=True)

# ── Scraper controls ──────────────────────────────────────────────────────────
status = load_status()

# Orphan process detection: if state=running but PID is dead → reset to error
if status.state == "running" and not is_pid_alive(status.pid):
    status.state = "error"
    status.message = "Scraper process died unexpectedly"
    save_status(status)
    status = load_status()

col1, col2, col3 = st.columns([2, 2, 3])

with col1:
    run_disabled = status.state == "running"
    if st.button("▶️ Run Scraper", disabled=run_disabled, type="primary"):
        # Spawn subprocess — dashboard does NOT block
        proc = subprocess.Popen(
            SCRAPER_CMD,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        from sourcing.models import ScraperStatus as _SS
        new_status = _SS(
            state="running",
            pid=proc.pid,
            message="Scraper starting...",
        )
        save_status(new_status)
        st.rerun()

with col2:
    if st.button("🔄 Re-score (no rescrape)"):
        try:
            engine = ScoringEngine(spec=spec, data_dir=DATA_DIR)
            scored = engine.score_all()
            st.success(
                f"Re-scored: {len(engine.complete)} complete, "
                f"{len(engine.incomplete)} incomplete"
            )
        except FileNotFoundError:
            st.error("No raw data found. Run the scraper first.")
        except Exception as e:
            st.error(f"Re-score failed: {e}")

with col3:
    if status.state == "error":
        if st.button("🔁 Reset scraper status"):
            reset_status()
            st.rerun()

# ── Status display ────────────────────────────────────────────────────────────
if status.state == "running":
    st.info(
        f"⏳ **Scraper running** (PID {status.pid}) — "
        f"{status.fetched} fetched · {status.message}"
    )
    # Auto-refresh every POLL_INTERVAL_S seconds
    time.sleep(POLL_INTERVAL_S)
    st.rerun()

elif status.state == "done":
    st.success(
        f"✅ **Last run complete** — {status.total} listings · {status.message}"
        + (f" · {status.finished_at[:19].replace('T', ' ')}" if status.finished_at else "")
    )
elif status.state == "error":
    st.error(
        f"❌ **Scraper error:** {status.message or status.last_error}. "
        "Use Reset button above."
    )

# ── Filters ───────────────────────────────────────────────────────────────────
st.divider()

all_scored = load_scored(os.path.join(DATA_DIR, "scored"))

if not all_scored:
    st.info("No results yet. Click **▶️ Run Scraper** to fetch listings.")
    st.stop()

# Filter controls
fcol1, fcol2, fcol3 = st.columns(3)
with fcol1:
    region_filter = st.multiselect(
        "Filter by region",
        options=spec.regions + ["Other"],
        default=[],
    )
with fcol2:
    min_score_filter = st.slider("Minimum score", 0, 100, 40)
with fcol3:
    show_duplicates = st.checkbox("Show possible duplicates", value=False)

# Separate complete vs incomplete
complete_all = [l for l in all_scored if not l.missing_required and l.status != "not_found"]
incomplete_all = [l for l in all_scored if l.missing_required]

# Apply filters to complete listings
complete_filtered = complete_all
if region_filter:
    complete_filtered = [
        l for l in complete_filtered if l.listing.region in region_filter
    ]
complete_filtered = [l for l in complete_filtered if l.score >= min_score_filter]
if not show_duplicates:
    complete_filtered = [l for l in complete_filtered if not l.possible_duplicate_of]

# ── Results ───────────────────────────────────────────────────────────────────
st.subheader(f"📊 Ranked Candidates — {len(complete_filtered)} shown")

if complete_filtered:
    render_listings_table(complete_filtered, "Complete listings")
else:
    st.info("No complete listings match the current filters.")

# Incomplete section (always shown)
if incomplete_all:
    with st.expander(f"⚠️ Incomplete listings ({len(incomplete_all)}) — missing required fields"):
        st.caption(
            "These listings are missing required fields and cannot be scored. "
            "They are kept for reference."
        )
        inc_rows = []
        for l in incomplete_all:
            inc_rows.append({
                "Missing": ", ".join(l.missing_required),
                "Title": l.listing.title[:50] or "(no title)",
                "Region": l.listing.region or "?",
                "Source": l.source,
                "Link": l.url,
            })
        st.dataframe(
            pd.DataFrame(inc_rows),
            column_config={"Link": st.column_config.LinkColumn("Link")},
            hide_index=True,
            use_container_width=True,
        )

# Score breakdown details (expandable per listing)
if complete_filtered:
    with st.expander("🔍 Score breakdown details"):
        for l in complete_filtered[:20]:  # cap at 20 for performance
            st.write(
                f"**{l.listing.title[:50] or l.id}** — "
                f"Score: **{l.score}** | "
                + score_breakdown_bar(l.score_breakdown.model_dump(), l.score)
            )
