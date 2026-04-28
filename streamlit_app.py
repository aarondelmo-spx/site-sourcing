# -*- coding: utf-8 -*-
"""
SPX Site Sourcing Dashboard — Phase 1
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import List, Optional

import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sourcing.models import ScoredListing, load_spec
from sourcing.providers.csv_providers import CsvPezaProvider
from sourcing.scorer.engine import ScoringEngine
from sourcing.storage import load_scored, load_status, reset_status, save_status

SPEC_PATH = os.path.join(ROOT, "spec.yaml")
DATA_DIR = os.path.join(ROOT, "data")
SCRAPER_CMD = [sys.executable, "-m", "sourcing.scrapers.orchestrator", "--spec", SPEC_PATH]
POLL_INTERVAL_S = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_pid_alive(pid: Optional[int]) -> bool:
    """Check if a process is alive. Uses tasklist on Windows for reliability."""
    if pid is None:
        return False
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False


def peza_staleness_check() -> Optional[str]:
    peza_path = os.path.join(DATA_DIR, "peza_zones.csv")
    if not os.path.exists(peza_path):
        return "⚠️ **PEZA zones data missing** — PEZA scoring disabled. Add `data/peza_zones.csv` to enable."
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
    parts = [f"{k.replace('_', ' ')}: {v:.0f}" for k, v in breakdown.items() if v > 0]
    return " | ".join(parts) if parts else "-"


def render_listings_table(listings: List[ScoredListing], title: str) -> None:
    if not listings:
        return
    import pandas as pd
    st.markdown(f"### {title} ({len(listings)})")
    rows = []
    for l in listings:
        dup_note = f"⚠️ dup of {l.possible_duplicate_of}" if l.possible_duplicate_of else ""
        rows.append({
            "Score": f"{l.score:.0f}",
            "Title": l.listing.title[:60] or "(no title)",
            "Region": l.listing.region or "?",
            "sqft": f"{l.listing.sqft:,.0f}" if l.listing.sqft else "—",
            "Docks": str(l.listing.dock_doors) if l.listing.dock_doors is not None else "—",
            "Height m": f"{l.listing.clear_height_m:.1f}" if l.listing.clear_height_m else "—",
            "Flood": (l.enriched.flood_risk or "?").upper(),
            "PEZA km": f"{l.enriched.peza_zone_km:.1f}" if l.enriched.peza_zone_km else "—",
            "Price PHP": f"{l.listing.price_php:,.0f}" if l.listing.price_php else "—",
            "Source": l.source,
            "Flag": dup_note,
            "Link": l.url,
        })
    st.dataframe(
        pd.DataFrame(rows),
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

peza_warn = peza_staleness_check()
if peza_warn:
    st.warning(peza_warn)

try:
    spec = load_spec(SPEC_PATH)
except Exception as e:
    st.error(f"**spec.yaml error:** {e}")
    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────────

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
    import pandas as pd
    st.dataframe(
        pd.DataFrame({
            "Field": ["sqft", "dock_doors", "clear_height_m", "region",
                      "corridor_access", "peza_zone", "max_flood_risk"],
            "Weight": [w.sqft, w.dock_doors, w.clear_height_m, w.region,
                       w.corridor_access, w.peza_zone, w.max_flood_risk],
        }),
        hide_index=True,
        use_container_width=True,
    )

# ── Scraper controls ──────────────────────────────────────────────────────────
# Resolve orphan before rendering buttons so state is accurate on first load

status = load_status()
if status.state == "running" and not is_pid_alive(status.pid):
    status.state = "error"
    status.message = "Scraper process ended — possibly interrupted. Reset to run again."
    save_status(status)

col1, col2, col3 = st.columns([2, 2, 3])

with col1:
    if st.button("▶️ Run Scraper", disabled=(status.state == "running"), type="primary"):
        proc = subprocess.Popen(
            SCRAPER_CMD,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        from sourcing.models import ScraperStatus as _SS
        save_status(_SS(state="running", pid=proc.pid, message="Scraper starting..."))
        st.rerun()

with col2:
    if st.button("🔄 Re-score (no rescrape)"):
        try:
            engine = ScoringEngine(spec=spec, data_dir=DATA_DIR)
            scored = engine.score_all()
            st.success(f"Re-scored: {len(engine.complete)} ranked, {len(engine.incomplete)} incomplete")
        except FileNotFoundError:
            st.error("No raw data found. Run the scraper first.")
        except Exception as e:
            st.error(f"Re-score failed: {e}")

with col3:
    if status.state == "error":
        if st.button("🔁 Reset and run again"):
            reset_status()
            st.rerun()

# ── Status + polling ──────────────────────────────────────────────────────────

@st.fragment(run_every=POLL_INTERVAL_S if status.state == "running" else None)
def status_panel():
    s = load_status()
    # Orphan check inside fragment so it updates during polling too
    if s.state == "running" and not is_pid_alive(s.pid):
        s.state = "error"
        s.message = "Scraper process ended — possibly interrupted. Reset to run again."
        save_status(s)
        s = load_status()

    if s.state == "running":
        st.info(
            f"⏳ **Scraper running** (PID {s.pid}) — "
            f"{s.fetched} fetched · {s.message}"
        )
    elif s.state == "done":
        st.success(
            f"✅ **Last run complete** — {s.total} listings · {s.message}"
            + (f" · finished {s.finished_at[:19].replace('T', ' ')}" if s.finished_at else "")
        )
    elif s.state == "error":
        st.error(f"❌ **{s.message}** — use Reset button above.")

status_panel()

# ── Results ───────────────────────────────────────────────────────────────────

st.divider()
all_scored = load_scored(os.path.join(DATA_DIR, "scored"))

if not all_scored:
    if status.state != "running":
        st.info("No results yet. Click **▶️ Run Scraper** to fetch listings.")
    st.stop()

# Filters
fcol1, fcol2, fcol3 = st.columns(3)
with fcol1:
    region_filter = st.multiselect("Filter by region", options=spec.regions + ["Other"], default=[])
with fcol2:
    min_score_filter = st.slider("Minimum score", 0, 100, 40)
with fcol3:
    show_duplicates = st.checkbox("Show possible duplicates", value=False)

complete_all = [l for l in all_scored if not l.missing_required and l.status != "not_found"]
incomplete_all = [l for l in all_scored if l.missing_required]

complete_filtered = complete_all
if region_filter:
    complete_filtered = [l for l in complete_filtered if l.listing.region in region_filter]
complete_filtered = [l for l in complete_filtered if l.score >= min_score_filter]
if not show_duplicates:
    complete_filtered = [l for l in complete_filtered if not l.possible_duplicate_of]

st.subheader(f"📊 Ranked Candidates — {len(complete_filtered)} shown")

if complete_filtered:
    render_listings_table(complete_filtered, "Complete listings")
else:
    st.info("No complete listings match the current filters.")

if incomplete_all:
    with st.expander(f"⚠️ Incomplete listings ({len(incomplete_all)}) — missing required fields"):
        st.caption("Missing required fields — kept for reference, not scored.")
        inc_rows = [
            {
                "Missing": ", ".join(l.missing_required),
                "Title": l.listing.title[:50] or "(no title)",
                "Region": l.listing.region or "?",
                "Source": l.source,
                "Link": l.url,
            }
            for l in incomplete_all
        ]
        st.dataframe(
            pd.DataFrame(inc_rows),
            column_config={"Link": st.column_config.LinkColumn("Link")},
            hide_index=True,
            use_container_width=True,
        )

if complete_filtered:
    with st.expander("🔍 Score breakdown details"):
        for l in complete_filtered[:20]:
            st.write(
                f"**{l.listing.title[:50] or l.id}** — Score: **{l.score}** | "
                + score_breakdown_bar(l.score_breakdown.model_dump(), l.score)
            )
