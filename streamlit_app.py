# -*- coding: utf-8 -*-
"""
SPX Site Sourcing Dashboard -- Phase 1
Filter-first UX: all scraped results, sidebar filters, map + table views.

Auth: set SPX_DASHBOARD_PASSWORD env-var to enable password gate when sharing
the URL over the office network. Leave unset for open localhost access.
"""
from __future__ import annotations

import html as _html
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

try:
    from folium.plugins import MarkerCluster
    _HAS_CLUSTER = True
except ImportError:
    _HAS_CLUSTER = False

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sourcing.models import ScoredListing, load_spec
from sourcing.providers.csv_providers import CsvPezaProvider
from sourcing.requirements import (
    ExpansionRequirement,
    load_requirements,
    new_requirement,
    parse_requirement_nl,
    save_requirement,
)
from sourcing.scorer.engine import ScoringEngine
from sourcing.storage import load_scored, load_status, reset_status, save_status

_HAS_ANTHROPIC = bool(os.environ.get("ANTHROPIC_API_KEY", ""))

SPEC_PATH      = os.path.join(ROOT, "spec.yaml")
DATA_DIR       = os.path.join(ROOT, "data")
PIPELINE_PATH  = os.path.join(DATA_DIR, "pipeline.json")
AUDIT_PATH     = os.path.join(DATA_DIR, "pipeline_audit.jsonl")
SCRAPER_CMD    = [sys.executable, "-m", "sourcing.scrapers.orchestrator", "--spec", SPEC_PATH]
POLL_INTERVAL_S = 3

PIPELINE_STATUSES = [
    "Prospect", "Contacted", "Site Visit", "LOI / Negotiating", "Signed", "Rejected",
]

PIPELINE_STATUS_COLORS = {
    "Prospect":          "#95a5a6",
    "Contacted":         "#3498db",
    "Site Visit":        "#f39c12",
    "LOI / Negotiating": "#e67e22",
    "Signed":            "#27ae60",
    "Rejected":          "#e74c3c",
}

# Major PH expressway polylines — 10+ waypoints each for smooth rendering
# Coordinates: (lat, lng) along the actual road alignment
CORRIDOR_LINES = {
    # South Luzon Expressway: Buendia → Calamba (~65 km)
    "SLEX": [
        (14.5547, 121.0244), (14.5300, 121.0280), (14.5000, 121.0350),
        (14.4700, 121.0370), (14.4319, 121.0342), (14.4000, 121.0450),
        (14.3700, 121.0550), (14.3300, 121.0700), (14.2900, 121.0900),
        (14.2500, 121.1100), (14.2100, 121.1600), (14.1700, 121.2000),
    ],
    # North Luzon Expressway: Balintawak → Tarlac (~130 km)
    "NLEX": [
        (14.6507, 121.0347), (14.6800, 121.0100), (14.7100, 120.9700),
        (14.7500, 120.9300), (14.8000, 120.9000), (14.8600, 120.8500),
        (14.9300, 120.8100), (15.0200, 120.7500), (15.1100, 120.7000),
        (15.2200, 120.6500), (15.3500, 120.6200), (15.4770, 120.5960),
    ],
    # Skyway / SLEX elevated: Alabang ↔ Buendia
    "Skyway": [
        (14.4319, 121.0342), (14.4600, 121.0310), (14.4900, 121.0275),
        (14.5150, 121.0250), (14.5300, 121.0220), (14.5547, 121.0244),
    ],
    # CAVITEX (Coastal Road): Paranaque → Kawit
    "CAVITEX": [
        (14.5000, 120.9900), (14.4800, 120.9780), (14.4600, 120.9680),
        (14.4400, 120.9580), (14.4200, 120.9480), (14.4000, 120.9300),
    ],
    # STAR Tollway: Sto. Tomas → Batangas City (~50 km)
    "STAR": [
        (14.1050, 121.1300), (14.0700, 121.1380), (14.0300, 121.1450),
        (13.9800, 121.1500), (13.9400, 121.1580), (13.8900, 121.1680),
        (13.8400, 121.1800), (13.8000, 121.1900), (13.7565, 121.0584),
    ],
    # C5 (Circumferential Rd 5): QC → Taguig
    "C5": [
        (14.6890, 121.0580), (14.6600, 121.0680), (14.6300, 121.0820),
        (14.6000, 121.0880), (14.5800, 121.0820), (14.5500, 121.0600),
        (14.5200, 121.0620), (14.4900, 121.0550), (14.4600, 121.0200),
    ],
    # SCTEX: Subic → Clark → Tarlac junction (~100 km)
    "SCTEX": [
        (14.8200, 120.2700), (14.8700, 120.3100), (14.9300, 120.3700),
        (15.0200, 120.4400), (15.1000, 120.5000), (15.1800, 120.5500),
        (15.2800, 120.5700), (15.3800, 120.5800), (15.4770, 120.5960),
    ],
    # TPLEX: Tarlac → Rosales → Pozorrubio (~100 km)
    "TPLEX": [
        (15.4770, 120.5960), (15.5800, 120.5980), (15.6800, 120.6050),
        (15.7800, 120.6150), (15.8800, 120.6200), (15.9800, 120.6000),
        (16.0800, 120.5500), (16.1400, 120.4900),
    ],
}
CORRIDOR_COLORS = {
    "SLEX":    "#e74c3c",   # red
    "NLEX":    "#3498db",   # blue
    "Skyway":  "#c0392b",   # dark red (elevated SLEX)
    "CAVITEX": "#e67e22",   # orange
    "STAR":    "#e74c3c",   # red (connects to SLEX)
    "C5":      "#2ecc71",   # green
    "SCTEX":   "#9b59b6",   # purple
    "TPLEX":   "#1abc9c",   # teal
}
CORRIDOR_WEIGHTS = {
    "SLEX": 4, "NLEX": 4, "Skyway": 3, "CAVITEX": 3,
    "STAR": 3, "C5": 3, "SCTEX": 3, "TPLEX": 3,
}


# ── Pipeline storage + audit ──────────────────────────────────────────────────

def load_pipeline() -> Dict[str, dict]:
    if not os.path.exists(PIPELINE_PATH):
        return {}
    try:
        with open(PIPELINE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_pipeline(data: Dict[str, dict]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PIPELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def log_pipeline_change(listing_id: str, old_status: str, new_status: str) -> None:
    """Append a status-change event to pipeline_audit.jsonl."""
    if old_status == new_status:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    # Rotate at 10 MB to prevent unbounded growth
    if os.path.exists(AUDIT_PATH) and os.path.getsize(AUDIT_PATH) > 10 * 1024 * 1024:
        os.rename(AUDIT_PATH, AUDIT_PATH + ".bak")
    entry = {
        "ts":   datetime.now(timezone.utc).isoformat(),
        "id":   listing_id,
        "from": old_status,
        "to":   new_status,
    }
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Auth gate ────────────────────────────────────────────────────────────────
# Set SPX_DASHBOARD_PASSWORD env-var to require a password.
# Leave unset (default) for open localhost access.

_AUTH_PASSWORD = os.environ.get("SPX_DASHBOARD_PASSWORD", "")


def _auth_gate() -> None:
    """Show login form and st.stop() if not authenticated."""
    if not _AUTH_PASSWORD:
        return  # No password configured — open access
    if st.session_state.get("_spx_authenticated"):
        return  # Already logged in
    st.set_page_config(page_title="SPX Login", page_icon="🔒", layout="centered")
    st.markdown("<br><br>", unsafe_allow_html=True)
    c = st.container()
    with c:
        st.title("🔒 SPX Site Sourcing")
        st.caption("Enter the team password to continue.")
        pwd = st.text_input("Password", type="password", label_visibility="collapsed",
                             placeholder="Team password")
        if st.button("Login", type="primary", use_container_width=True):
            if pwd == _AUTH_PASSWORD:
                st.session_state._spx_authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()


_auth_gate()


# ── Page setup ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SPX Site Sourcing",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Scoped CSS — .spx- prefix prevents bleed across tabs
st.markdown("""
<style>
/* Tighten metric padding */
[data-testid="stMetricValue"] { font-size: 1.3rem; }
/* Tighter expander content */
.streamlit-expanderContent { padding-top: 0.4rem !important; }
/* Score badge in table */
.spx-score-badge {
    display:inline-block; border-radius:10px;
    padding:2px 9px; color:white; font-weight:700; font-size:13px;
}
/* Pipeline status pill */
.spx-status-pill {
    display:inline-block; border-radius:14px;
    padding:4px 12px; color:white; font-size:12px; font-weight:600;
    text-align:center; width:100%;
}
/* New listing row highlight */
.spx-new-row { background-color: #fffde7 !important; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_pid_alive(pid: Optional[int]) -> bool:
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
        return "WARNING: **PEZA zones data missing** -- PEZA scoring disabled."
    try:
        provider = CsvPezaProvider(peza_path)
        if provider.is_stale():
            days = provider.days_since_update()
            return f"WARNING: **PEZA zones CSV is {days} days old** (threshold: 90 days)."
    except Exception:
        pass
    return None


def score_color(score: Optional[float]) -> str:
    if score is None:   return "#95a5a6"
    if score >= 70:     return "#27ae60"
    if score >= 50:     return "#f39c12"
    if score >= 30:     return "#e67e22"
    return "#e74c3c"


def build_popup(l: ScoredListing) -> str:
    """Clean white card popup — all scraped strings HTML-escaped."""
    corridors = l.enriched.corridor_distances_km or {}
    slex = corridors.get("SLEX")
    nlex = corridors.get("NLEX")

    title   = _html.escape(l.listing.title[:70] or "(no title)")
    address = _html.escape(l.listing.address or "?")
    color   = score_color(l.score)
    score_str = f"{l.score:.0f}" if l.score is not None else "—"
    sqm_str   = f"{l.listing.sqm:,.0f} sqm" if l.listing.sqm else "—"
    docks_str = str(l.listing.dock_doors) if l.listing.dock_doors is not None else "—"
    price_str = f"PHP {l.listing.price_php:,.0f}/mo" if l.listing.price_php else "—"
    slex_str  = f"{slex:.1f} km" if slex else "—"
    nlex_str  = f"{nlex:.1f} km" if nlex else "—"
    flood_str = (l.enriched.flood_risk or "?").upper()
    region_str = _html.escape(l.listing.region or "?")
    url = _html.escape(l.url)

    missing_html = (
        f"<div style='margin-top:6px;color:#e74c3c;font-size:11px'>"
        f"⚠ Missing: {_html.escape(', '.join(l.missing_required))}</div>"
    ) if l.missing_required else ""
    dup_html = (
        f"<div style='color:#e67e22;font-size:11px'>"
        f"🔁 Possible dup of {_html.escape(l.possible_duplicate_of)}</div>"
    ) if l.possible_duplicate_of else ""
    new_html = "<div style='color:#f39c12;font-size:11px'>🆕 New since last run</div>" if l.is_new else ""

    return f"""
<div style='font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
            width:280px;padding:14px;background:white;
            border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.15)'>
  <div style='font-size:13px;font-weight:700;line-height:1.3;margin-bottom:3px'>{title}</div>
  <div style='font-size:11px;color:#888;margin-bottom:10px'>{address}</div>
  <div style='display:flex;align-items:center;gap:8px;margin-bottom:10px'>
    <span style='background:{color};color:white;border-radius:20px;
                 padding:3px 12px;font-weight:800;font-size:16px'>{score_str}</span>
    <span style='font-size:12px;color:#555'>{region_str}</span>
  </div>
  <table style='font-size:12px;width:100%;border-collapse:collapse'>
    <tr style='border-bottom:1px solid #f0f0f0'>
      <td style='color:#999;padding:3px 0;width:45%'>Floor area</td>
      <td style='font-weight:600'>{sqm_str}</td>
    </tr>
    <tr style='border-bottom:1px solid #f0f0f0'>
      <td style='color:#999;padding:3px 0'>Dock doors</td>
      <td style='font-weight:600'>{docks_str}</td>
    </tr>
    <tr style='border-bottom:1px solid #f0f0f0'>
      <td style='color:#999;padding:3px 0'>Price/mo</td>
      <td style='font-weight:600'>{price_str}</td>
    </tr>
    <tr style='border-bottom:1px solid #f0f0f0'>
      <td style='color:#999;padding:3px 0'>SLEX</td>
      <td>{slex_str}</td>
    </tr>
    <tr>
      <td style='color:#999;padding:3px 0'>Flood</td>
      <td>{flood_str}</td>
    </tr>
  </table>
  {missing_html}{dup_html}{new_html}
  <a href='{url}' target='_blank'
     style='display:block;margin-top:10px;padding:6px;background:#f8f9fa;
            border-radius:6px;font-size:12px;color:#3498db;text-decoration:none;
            text-align:center;font-weight:600'>Open listing →</a>
</div>"""


@st.cache_data(show_spinner=False)
def _build_map_cached(
    _cache_key: tuple,          # drives cache invalidation (listing IDs + scores)
    _listings: list,            # underscore = skipped by streamlit hasher; _cache_key does the work
    show_corridors: bool,
    show_peza: bool,
    peza_path: str,
) -> tuple:
    """
    Build the folium map object.  Cached by listing IDs + scores so it
    is only rebuilt when the filtered set or a score changes, not on every
    unrelated sidebar interaction.

    Returns (folium.Map, int mapped_count).
    """
    m = folium.Map(
        location=[14.40, 121.00],   # centred on Metro Manila + CALABARZON
        zoom_start=9,
        tiles="CartoDB positron",
        prefer_canvas=True,         # faster canvas renderer for many markers
    )

    # ── Highway corridors (FeatureGroup so LayerControl can toggle them) ────
    hw_group = folium.FeatureGroup(name="Highways", show=show_corridors)
    for name, points in CORRIDOR_LINES.items():
        color  = CORRIDOR_COLORS[name]
        weight = CORRIDOR_WEIGHTS.get(name, 3)
        folium.PolyLine(
            locations=points,
            color=color,
            weight=weight,
            opacity=0.75,
            tooltip=folium.Tooltip(name, sticky=False),
        ).add_to(hw_group)
        # Label at midpoint
        mid = points[len(points) // 2]
        folium.Marker(
            location=mid,
            icon=folium.DivIcon(
                html=(f"<div style='background:{color};color:white;font-size:10px;"
                      f"font-weight:700;font-family:sans-serif;padding:2px 6px;"
                      f"border-radius:3px;white-space:nowrap;opacity:0.9'>{name}</div>"),
                icon_size=(60, 18),
                icon_anchor=(30, 9),
            ),
        ).add_to(hw_group)
    hw_group.add_to(m)

    # ── PEZA zones ──────────────────────────────────────────────────────────
    peza_group = folium.FeatureGroup(name="PEZA Zones", show=show_peza)
    if os.path.exists(peza_path):
        try:
            import csv
            with open(peza_path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    lat = float(row.get("lat") or 0)
                    lng = float(row.get("lng") or 0)
                    if lat and lng:
                        folium.CircleMarker(
                            location=[lat, lng],
                            radius=14,
                            color="#9b59b6",
                            weight=2,
                            fill=True,
                            fill_color="#9b59b6",
                            fill_opacity=0.25,
                            tooltip=folium.Tooltip(
                                _html.escape(row.get("name", "PEZA zone")),
                                sticky=False,
                            ),
                        ).add_to(peza_group)
        except Exception:
            pass
    peza_group.add_to(m)

    # ── Listing markers — CircleMarker (no CDN, fast SVG) ───────────────────
    listings_group = folium.FeatureGroup(name="Listings", show=True)
    target = (
        MarkerCluster(
            options={"spiderfyOnMaxZoom": True, "showCoverageOnHover": False,
                     "maxClusterRadius": 40},
        ).add_to(listings_group)
        if _HAS_CLUSTER else listings_group
    )

    mapped = 0
    for l in _listings:
        lat, lng = l.listing.lat, l.listing.lng
        if lat is None or lng is None:
            continue
        mapped += 1
        color = score_color(l.score)
        score = l.score or 0
        # Radius encodes quality: bigger = higher score
        radius = 13 if score >= 70 else (10 if score >= 50 else (8 if score >= 30 else 6))
        # Thicker border for duplicates so they stand out
        border_weight = 1 if not l.possible_duplicate_of else 3
        border_color  = "white" if not l.possible_duplicate_of else "#f39c12"

        tooltip_str = (
            f"<b>{_html.escape(l.listing.title[:45] or l.id)}</b><br>"
            f"Score: {score:.0f}/100 &nbsp;|&nbsp; "
            f"{_html.escape(l.listing.region or '?')}"
        )

        folium.CircleMarker(
            location=[lat, lng],
            radius=radius,
            color=border_color,
            weight=border_weight,
            fill=True,
            fill_color=color,
            fill_opacity=0.88,
            popup=folium.Popup(build_popup(l), max_width=320, lazy=True),
            tooltip=folium.Tooltip(tooltip_str, sticky=False),
        ).add_to(target)

    listings_group.add_to(m)

    # LayerControl — collapsed to keep the map clean
    folium.LayerControl(collapsed=True).add_to(m)

    return m, mapped


def build_map(listings: List[ScoredListing], show_corridors: bool, show_peza: bool):
    """Public wrapper — builds a hashable cache key then delegates to the cached builder."""
    cache_key = tuple(
        (l.id, l.score, bool(l.possible_duplicate_of), bool(l.is_new))
        for l in listings
        if l.listing.lat is not None and l.listing.lng is not None
    )
    peza_path = os.path.join(DATA_DIR, "peza_zones.csv")
    return _build_map_cached(cache_key, listings, show_corridors, show_peza, peza_path)


def listings_to_df(listings: List[ScoredListing]) -> pd.DataFrame:
    rows = []
    for l in listings:
        corridors = l.enriched.corridor_distances_km or {}
        rows.append({
            "New":       "🆕" if l.is_new else "",
            "Score":     l.score if l.score is not None else None,
            "Title":     l.listing.title[:60] or "(no title)",
            "Region":    l.listing.region or "?",
            "sqm":       l.listing.sqm,
            "Docks":     l.listing.dock_doors,
            "Height m":  l.listing.clear_height_m,
            "SLEX km":   corridors.get("SLEX"),
            "NLEX km":   corridors.get("NLEX"),
            "C5 km":     corridors.get("C5"),
            "PEZA km":   l.enriched.peza_zone_km,
            "Flood":     (l.enriched.flood_risk or "?").upper(),
            "Price PHP": l.listing.price_php,
            "Agent":     l.listing.agent_name or "",
            "Missing":   ", ".join(l.missing_required) if l.missing_required else "",
            "Dup":       "yes" if l.possible_duplicate_of else "",
            "Source":    l.source,
            "ID":        l.id,
            "Link":      l.url,
        })
    return pd.DataFrame(rows)


# ── Page header ───────────────────────────────────────────────────────────────

st.markdown(
    "<h2 style='margin-bottom:0'>🏭 SPX Site Sourcing</h2>"
    "<p style='color:#888;margin-top:2px;margin-bottom:0'>Philippines nationwide warehouse & logistics hub sourcing</p>",
    unsafe_allow_html=True,
)

peza_warn = peza_staleness_check()
if peza_warn and not st.session_state.get("_peza_warn_dismissed"):
    _wcols = st.columns([20, 1])
    _wcols[0].warning(peza_warn)
    if _wcols[1].button("✕", key="_dismiss_peza", help="Dismiss for this session"):
        st.session_state["_peza_warn_dismissed"] = True
        st.rerun()

try:
    spec = load_spec(SPEC_PATH)
except Exception as e:
    st.error(f"**spec.yaml error:** {e}")
    st.stop()

# ── Active requirement banner ─────────────────────────────────────────────────
_active_req_main: Optional[ExpansionRequirement] = st.session_state.get("active_requirement")
if _active_req_main:
    _bcols = st.columns([20, 1])
    _bcols[0].info(
        f"🎯 **Active requirement:** {_active_req_main.summary_line()}  "
        f"· Filters set automatically from this requirement."
    )
    if _bcols[1].button("✕", key="_dismiss_req", help="Clear active requirement"):
        st.session_state.pop("active_requirement", None)
        st.rerun()

# ── Scraper controls ──────────────────────────────────────────────────────────

status = load_status()
if status.state == "running" and not is_pid_alive(status.pid):
    status.state = "error"
    status.message = "Scraper process ended -- possibly interrupted. Reset to run again."
    save_status(status)

col1, col2, col3 = st.columns([2, 2, 3])
with col1:
    scrape_all_regions = st.toggle(
        "🌏 Scrape all PH", value=False,
        help="When ON, scrapes all known Philippine provinces (~1 hr). OFF = spec regions only.",
    )
    _cmd = SCRAPER_CMD + (["--all-regions"] if scrape_all_regions else [])
    if st.button("▶️ Run Scraper", disabled=(status.state == "running"), type="primary"):
        proc = subprocess.Popen(_cmd, cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        from sourcing.models import ScraperStatus as _SS
        save_status(_SS(state="running", pid=proc.pid, message="Scraper starting..."))
        st.rerun()

with col2:
    if st.button("🔄 Re-score"):
        try:
            engine = ScoringEngine(spec=spec, data_dir=DATA_DIR)
            engine.score_all()
            st.success(f"Re-scored: {len(engine.complete)} ranked, "
                       f"{len(engine.incomplete)} incomplete, "
                       f"{engine.dedup_count} duplicates flagged")
        except FileNotFoundError:
            st.error("No raw data found. Run the scraper first.")
        except Exception as e:
            st.error(f"Re-score failed: {e}")

with col3:
    if status.state == "error":
        if st.button("🔁 Reset and run again"):
            reset_status()
            st.rerun()
    if _AUTH_PASSWORD and st.session_state.get("_spx_authenticated"):
        if st.button("🔒 Logout"):
            del st.session_state["_spx_authenticated"]
            st.rerun()


@st.fragment(run_every=POLL_INTERVAL_S if status.state == "running" else None)
def status_panel():
    s = load_status()
    if s.state == "running" and not is_pid_alive(s.pid):
        s.state = "error"
        s.message = "Scraper process ended -- possibly interrupted. Reset to run again."
        save_status(s)
        s = load_status()
    if s.state == "running":
        st.info(f"⏳ **Scraper running** (PID {s.pid}) — {s.fetched} fetched · {s.message}")
    elif s.state == "done":
        st.success(
            f"✅ **Last run complete** — {s.total} listings · {s.message}"
            + (f" · {s.finished_at[:19].replace('T',' ')}" if s.finished_at else "")
        )
    elif s.state == "error":
        st.error(f"❌ **{s.message}** — use Reset button above.")


status_panel()

# ── Load data ─────────────────────────────────────────────────────────────────

st.divider()
all_scored = load_scored(os.path.join(DATA_DIR, "scored"))

if not all_scored:
    if status.state != "running":
        st.markdown(
            "<div style='text-align:center;padding:40px;color:#888'>"
            "<div style='font-size:48px'>🏭</div>"
            "<div style='font-size:18px;font-weight:600;margin-top:8px'>No listings yet</div>"
            "<div style='margin-top:4px'>Click <b>▶️ Run Scraper</b> above to fetch listings.</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    st.stop()

all_sqm     = [l.listing.sqm for l in all_scored if l.listing.sqm]
all_price   = [l.listing.price_php for l in all_scored if l.listing.price_php]
all_docks   = [l.listing.dock_doors for l in all_scored if l.listing.dock_doors is not None]
all_regions = sorted({l.listing.region for l in all_scored if l.listing.region})
sqm_max_data = int(max(all_sqm)) if all_sqm else 20000
sqm_slider_max = max(sqm_max_data, int(spec.max_sqm) + 1000)

# ── Sidebar filters ───────────────────────────────────────────────────────────

with st.sidebar:
    # ── Requirements intake ───────────────────────────────────────────────────
    with st.expander("🎯 Active Requirement", expanded=True):
        saved_reqs = load_requirements(DATA_DIR)
        req_names  = [f"{r.project_name} ({r.created_at[:10]})" for r in saved_reqs]

        _active_req: Optional[ExpansionRequirement] = st.session_state.get("active_requirement")

        # Selector for saved requirements
        if saved_reqs:
            _sel_idx = st.selectbox(
                "Load saved requirement",
                options=["— none —"] + req_names,
                index=(0 if _active_req is None else
                       next((i+1 for i, r in enumerate(saved_reqs)
                             if r.requirement_id == _active_req.requirement_id), 0)),
                key="sb_req_selector",
            )
            if _sel_idx != "— none —" and "— none —" not in _sel_idx:
                chosen_idx = req_names.index(_sel_idx)
                loaded = saved_reqs[chosen_idx]
                if (_active_req is None or
                        loaded.requirement_id != _active_req.requirement_id):
                    st.session_state["active_requirement"] = loaded
                    _active_req = loaded
                    # Push requirement values into filter widgets
                    if loaded.region_priority:
                        valid_regions = [r for r in loaded.region_priority
                                         if r in all_regions]
                        st.session_state["sb_region"] = valid_regions
                    if loaded.sqm_min > 0 or loaded.sqm_max < sqm_slider_max:
                        st.session_state["sb_sqm"] = (
                            max(0, int(loaded.sqm_min)),
                            min(sqm_slider_max, int(loaded.sqm_max)),
                        )
                    if loaded.dock_doors_min > 0:
                        st.session_state["sb_dock"] = loaded.dock_doors_min
                    if loaded.clear_height_min > 0:
                        st.session_state["sb_height"] = loaded.clear_height_min
                    if loaded.slex_max_km < 60:
                        st.session_state["sb_slex"] = int(loaded.slex_max_km)
                    st.rerun()
            elif _sel_idx == "— none —" and _active_req is not None:
                st.session_state.pop("active_requirement", None)
                _active_req = None
                st.rerun()

        if _active_req:
            st.success(_active_req.summary_line())
            st.caption("Filters auto-set from requirement.")
        else:
            st.caption("No active requirement — filters are manual.")

        with st.form("new_req_form", clear_on_submit=False):
            st.markdown("**New requirement**")

            if _HAS_ANTHROPIC:
                nl_text = st.text_area(
                    "Describe in plain language (optional)",
                    placeholder="e.g. 3000 sqm warehouse in Laguna, 4 dock doors, under ₱180k/month",
                    height=68,
                    key="req_nl_input",
                )
            else:
                nl_text = ""
                st.info("Set `ANTHROPIC_API_KEY` to enable natural language intake.")

            proj_name    = st.text_input("Project name", value="Expansion Search", key="req_proj")
            c1, c2       = st.columns(2)
            req_sqm_min  = c1.number_input("sqm min", value=0, step=100, min_value=0, key="req_sqm_min")
            req_sqm_max  = c2.number_input("sqm max", value=5000, step=100, min_value=0, key="req_sqm_max")
            req_budget   = st.number_input("Max ₱/sqm/month (0=any)", value=0, step=50, min_value=0, key="req_budget")
            req_docks    = st.number_input("Min dock doors", value=0, step=1, min_value=0, key="req_docks")
            req_height   = st.number_input("Min clear height (m)", value=0.0, step=0.5, min_value=0.0, key="req_height")
            req_slex     = st.number_input("Max SLEX km (60=any)", value=60, step=5, min_value=0, max_value=200, key="req_slex")
            req_peza     = st.checkbox("PEZA required", value=False, key="req_peza")
            req_regions  = st.multiselect("Region priority", options=all_regions, key="req_regions")

            submitted = st.form_submit_button("💾 Save & Activate", type="primary", use_container_width=True)

        if submitted:
            if nl_text.strip() and _HAS_ANTHROPIC:
                with st.spinner("Parsing with AI…"):
                    parsed, err = parse_requirement_nl(nl_text.strip())
                if err:
                    st.warning(f"AI parse issue: {err}\nUsing form values.")
                    parsed = None
                else:
                    st.info("AI parsed your description — review fields below then save again if needed.")
            else:
                parsed = None

            if parsed:
                req = new_requirement(parsed.get("project_name") or proj_name)
                req.sqm_min             = parsed["sqm_min"]
                req.sqm_max             = parsed["sqm_max"]
                req.region_priority     = parsed["region_priority"] or req_regions
                req.budget_max_sqm_month = parsed["budget_max_sqm_month"]
                req.dock_doors_min      = parsed["dock_doors_min"]
                req.clear_height_min    = parsed["clear_height_min"]
                req.peza_required       = parsed["peza_required"]
                req.slex_max_km         = parsed["slex_max_km"]
                req.power_requirement_kva = parsed.get("power_requirement_kva")
                req.notes               = parsed.get("notes", "")
                # Show what was parsed
                with st.expander("📋 AI parsed values", expanded=True):
                    st.json({k: v for k, v in parsed.items() if v not in (None, "", [], 0, 0.0, False)})
            else:
                req = new_requirement(proj_name)
                req.sqm_min              = float(req_sqm_min)
                req.sqm_max              = float(req_sqm_max)
                req.region_priority      = req_regions
                req.budget_max_sqm_month = float(req_budget)
                req.dock_doors_min       = int(req_docks)
                req.clear_height_min     = float(req_height)
                req.peza_required        = req_peza
                req.slex_max_km          = float(req_slex)

            save_requirement(req, DATA_DIR)
            st.session_state["active_requirement"] = req

            # Push into filter widgets
            if req.region_priority:
                valid = [r for r in req.region_priority if r in all_regions]
                if valid:
                    st.session_state["sb_region"] = valid
            if req.sqm_max > 0:
                st.session_state["sb_sqm"] = (
                    max(0, int(req.sqm_min)),
                    min(sqm_slider_max, int(req.sqm_max)),
                )
            if req.dock_doors_min > 0:
                st.session_state["sb_dock"] = req.dock_doors_min
            if req.clear_height_min > 0:
                st.session_state["sb_height"] = req.clear_height_min
            if req.slex_max_km < 60:
                st.session_state["sb_slex"] = int(req.slex_max_km)
            if req.budget_max_sqm_month > 0 and req.sqm_max > 0 and all_price:
                budget_total = int(req.budget_max_sqm_month * req.sqm_max)
                st.session_state["sb_price"] = min(budget_total, int(max(all_price)))
            st.rerun()

    # ── Active filter count computed after widgets render — placeholder first
    _filter_header = st.empty()

    if st.button("✕ Clear all filters", key="clear_filters"):
        # Set keys to their default values explicitly so widgets re-render correctly.
        # Deleting keys alone leaves Streamlit's widget cache stale for one cycle.
        st.session_state["sb_region"] = []
        st.session_state["sb_sqm"]    = (0, sqm_slider_max)
        st.session_state["sb_dock"]   = 0
        st.session_state["sb_height"] = 0.0
        st.session_state["sb_slex"]   = 60
        if "sb_price" in st.session_state:
            del st.session_state["sb_price"]
        st.session_state["sb_show_dups"]        = False
        st.session_state["sb_show_incomplete"]  = True
        st.rerun()

    with st.expander("📍 Location", expanded=True):
        region_filter = st.multiselect(
            "Region", options=all_regions, default=[],
            help="Leave blank to show all regions", key="sb_region",
        )

    with st.expander("📐 Size & Features", expanded=True):
        sqm_range = st.slider(
            "Floor area (sqm)", 0, sqm_slider_max,
            (0, sqm_slider_max), step=50, key="sb_sqm",
        )
        include_unknown_sqm = st.checkbox("Include no-sqm listings", value=True)

        dock_min = st.slider(
            "Dock doors (min)", 0, max(max(all_docks) if all_docks else 0, 10),
            0, key="sb_dock",
        )
        include_unknown_docks = st.checkbox("Include no-dock listings", value=True)

        height_min = st.slider("Clear height min (m)", 0.0, 20.0, 0.0, step=0.5, key="sb_height")
        include_unknown_height = st.checkbox("Include no-height listings", value=True)

    with st.expander("🛣️ Access & Visibility", expanded=False):
        slex_max_km = st.slider(
            "Max distance to SLEX (km)", 0, 60, 60, key="sb_slex",
            help="Applies to Luzon only. Visayas/Mindanao listings are always shown regardless of this filter.",
        )
        include_unknown_slex = st.checkbox(
            "Include listings with no SLEX data", value=True,
            help="Includes listings where corridor distance could not be computed.",
        )
        show_duplicates = st.checkbox("Show possible duplicates", value=False, key="sb_show_dups")
        show_incomplete = st.checkbox("Show listings with missing fields", value=True, key="sb_show_incomplete")

    with st.expander("💰 Price", expanded=False):
        if all_price:
            price_max_filter = st.slider(
                "Max price (PHP/month)", 0, int(max(all_price)),
                int(max(all_price)), step=50000, key="sb_price",
            )
            include_unknown_price = st.checkbox("Include no-price listings", value=True)
        else:
            price_max_filter = None
            include_unknown_price = True

    with st.expander("🗺️ Map options", expanded=False):
        show_corridors  = st.checkbox("Show corridor roads", value=True)
        show_peza       = st.checkbox("Show PEZA zones", value=True)
        filter_by_map   = st.checkbox(
            "📍 Filter to map view", value=False,
            help="Pan/zoom the map, then tick this to restrict results to the visible area.",
        )
        if filter_by_map and "map_bounds" not in st.session_state:
            st.caption("Interact with the map first — bounds will be captured automatically.")

    with st.expander("⏰ Auto-scrape", expanded=False):
        _task_name = "SPX_Scraper"

        def _get_schedule_info() -> Optional[str]:
            try:
                r = subprocess.run(
                    ["schtasks", "/Query", "/TN", _task_name, "/FO", "LIST"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    for line in r.stdout.splitlines():
                        if "Next Run Time" in line or "Status" in line:
                            return line.strip()
            except Exception:
                pass
            return None

        sched_info = _get_schedule_info()
        if sched_info:
            st.caption(f"Scheduled: {sched_info}")

        sched_hour = st.selectbox("Run daily at (24h)", list(range(24)), index=6,
                                  format_func=lambda h: f"{h:02d}:00")

        if st.button("📅 Schedule daily scrape"):
            python_exe = sys.executable.replace("\\", "\\\\")
            script = os.path.join(ROOT, "run_scraper.py").replace("\\", "\\\\")
            cmd_str = (f'schtasks /Create /TN "{_task_name}" /TR '
                       f'"{python_exe} {script}" /SC DAILY /ST {sched_hour:02d}:00 /F')
            try:
                result = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    st.success(f"Scheduled daily at {sched_hour:02d}:00 ✓")
                else:
                    st.error(f"Schedule failed: {result.stderr or result.stdout}")
            except Exception as ex:
                st.error(f"Could not schedule: {ex}")

        if sched_info and st.button("🗑️ Remove schedule"):
            try:
                subprocess.run(["schtasks", "/Delete", "/TN", _task_name, "/F"],
                               timeout=5, check=True)
                st.success("Schedule removed.")
                st.rerun()
            except Exception as ex:
                st.error(f"Could not remove: {ex}")

    st.divider()
    st.caption(
        f"**Spec** · sqm {spec.min_sqm:,.0f}–{spec.max_sqm:,.0f} "
        f"· docks ≥{spec.dock_doors_min} · height ≥{spec.clear_height_m_min}m"
    )

    # Compute active filter count and backfill header
    _active_filters = sum([
        bool(region_filter),
        sqm_range != (0, sqm_slider_max),
        dock_min > 0,
        height_min > 0,
        slex_max_km < 60,
        bool(all_price and price_max_filter is not None and price_max_filter < int(max(all_price))),
    ])
    _filter_label = f"🔍 Filters ({_active_filters} active)" if _active_filters else "🔍 Filters"
    _filter_header.markdown(f"### {_filter_label}")

# ── Apply filters ─────────────────────────────────────────────────────────────

filtered = all_scored
if not show_incomplete:
    filtered = [l for l in filtered if not l.missing_required]
if not show_duplicates:
    filtered = [l for l in filtered if not l.possible_duplicate_of]
if region_filter:
    filtered = [l for l in filtered if l.listing.region in region_filter]


def sqm_ok(l):
    v = l.listing.sqm
    return include_unknown_sqm if v is None else sqm_range[0] <= v <= sqm_range[1]


def dock_ok(l):
    v = l.listing.dock_doors
    return include_unknown_docks if v is None else v >= dock_min


def height_ok(l):
    v = l.listing.clear_height_m
    return include_unknown_height if v is None else v >= height_min


def slex_ok(l):
    d = (l.enriched.corridor_distances_km or {}).get("SLEX")
    # > 200 km means the listing is outside Luzon (e.g. Cebu, Davao) — SLEX
    # distance is meaningless there, treat the same as "unknown".
    if d is None or d > 200:
        return include_unknown_slex
    return d <= slex_max_km


def price_ok(l):
    if price_max_filter is None:
        return True
    v = l.listing.price_php
    return include_unknown_price if v is None else v <= price_max_filter


filtered = [l for l in filtered if sqm_ok(l) and dock_ok(l) and height_ok(l) and slex_ok(l) and price_ok(l)]

if filter_by_map and "map_bounds" in st.session_state:
    b = st.session_state.map_bounds
    sw, ne = b.get("_southWest", {}), b.get("_northEast", {})
    lat_min, lat_max = sw.get("lat", -90), ne.get("lat", 90)
    lng_min, lng_max = sw.get("lng", -180), ne.get("lng", 180)
    filtered = [
        l for l in filtered
        if l.listing.lat is not None and l.listing.lng is not None
        and lat_min <= l.listing.lat <= lat_max
        and lng_min <= l.listing.lng <= lng_max
    ]

filtered.sort(key=lambda l: l.score if l.score is not None else -1, reverse=True)

# ── Summary header ────────────────────────────────────────────────────────────

mapped_count = sum(1 for l in filtered if l.listing.lat is not None)
new_count    = sum(1 for l in all_scored if l.is_new)
dup_count    = sum(1 for l in all_scored if l.possible_duplicate_of)
hidden_count = len(all_scored) - len(filtered)   # listings removed by active filters

_parts = [f"📊 **{len(filtered)}** listings", f"**{mapped_count}** on map",
          f"**{len(all_scored)}** total scraped"]
if hidden_count > 0 and hidden_count != dup_count:
    # Some non-duplicate listings are hidden by active filters — make it explicit
    _parts.append(f"🙈 **{hidden_count}** hidden by filters")

# Only show "new" badge when it's meaningful — suppress if every listing is new
# (first-run case: no previous finished_at to compare against)
new_rate = new_count / len(all_scored) if all_scored else 0
if new_count and new_rate < 0.9:
    _parts.append(f"🆕 **{new_count}** new")
elif new_count and new_rate >= 0.9:
    _parts.append("🆕 First run — all listings are new")

if dup_count:
    _parts.append(f"🔁 **{dup_count}** duplicates hidden")
st.markdown("  ·  ".join(_parts))

if not filtered:
    st.markdown(
        "<div style='text-align:center;padding:40px;color:#888'>"
        "<div style='font-size:36px'>🔍</div>"
        "<div style='font-size:16px;font-weight:600;margin-top:8px'>No listings match these filters</div>"
        "<div style='margin-top:4px'>Try relaxing the size or region filters in the sidebar.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.stop()

# ── View tabs  (Pipeline first — daily workflow) ──────────────────────────────

tab_pipeline, tab_map, tab_table, tab_breakdown = st.tabs(
    ["🏗️ Pipeline", "🗺️ Map", "📋 Table", "📈 Score breakdown"]
)

# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_pipeline:
    pipeline_data = load_pipeline()
    id_to_listing = {l.id: l for l in all_scored}

    # Build rows
    pipeline_rows = []
    for l in all_scored:
        p = pipeline_data.get(l.id, {})
        pipeline_rows.append({
            "ID":            l.id,
            "Status":        p.get("status", "Prospect"),
            "Title":         (l.listing.title[:55] or "(no title)"),
            "Region":        l.listing.region or "?",
            "sqm":           f"{l.listing.sqm:,.0f}" if l.listing.sqm else "—",
            "Score":         f"{l.score:.0f}" if l.score is not None else "—",
            "Agent":         l.listing.agent_name or p.get("contact_name", ""),
            "Contact phone": p.get("contact_phone", ""),
            "Notes":         p.get("notes", ""),
            "Link":          l.url,
        })

    pipeline_df = pd.DataFrame(pipeline_rows)
    status_counts = pipeline_df["Status"].value_counts()

    # Filtered-view pill counts (respect sidebar filters so NCR→NCR counts)
    filtered_ids = {l.id for l in filtered}
    filtered_pipeline_df = pipeline_df[pipeline_df["ID"].isin(filtered_ids)]
    filtered_status_counts = filtered_pipeline_df["Status"].value_counts()

    # Show filtered counts when a filter is active; global counts otherwise
    using_filtered = len(filtered) < len(all_scored)
    display_counts = filtered_status_counts if using_filtered else status_counts

    # Label so users know whether pills reflect filtered or full view
    if using_filtered:
        st.caption(f"Showing pipeline for **{len(filtered_pipeline_df)}** filtered listings "
                   f"({len(pipeline_df)} total across all regions)")

    # Coloured status count pills
    scols = st.columns(len(PIPELINE_STATUSES))
    for col, s in zip(scols, PIPELINE_STATUSES):
        c = PIPELINE_STATUS_COLORS[s]
        cnt = int(display_counts.get(s, 0))
        col.markdown(
            f"<div style='background:{c};color:white;border-radius:16px;"
            f"padding:6px 10px;text-align:center'>"
            f"<div style='font-size:11px;opacity:.9'>{s}</div>"
            f"<div style='font-size:22px;font-weight:800;line-height:1.1'>{cnt}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.divider()

    # Editable table
    edited = st.data_editor(
        pipeline_df,
        column_config={
            "ID":            st.column_config.TextColumn("ID", disabled=True, width="small"),
            "Status":        st.column_config.SelectboxColumn(
                "Status", options=PIPELINE_STATUSES, required=True, width="medium"
            ),
            "Title":         st.column_config.TextColumn("Title", disabled=True),
            "Region":        st.column_config.TextColumn("Region", disabled=True, width="small"),
            "sqm":           st.column_config.TextColumn("sqm", disabled=True, width="small"),
            "Score":         st.column_config.TextColumn("Score", disabled=True, width="small"),
            "Agent":         st.column_config.TextColumn("Agent", width="medium"),
            "Contact phone": st.column_config.TextColumn("Phone", width="medium"),
            "Notes":         st.column_config.TextColumn("Notes", width="large"),
            "Link":          st.column_config.LinkColumn("Link", width="small"),
        },
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        key="pipeline_editor",
    )

    if st.button("💾 Save pipeline", type="primary"):
        new_pipeline: Dict[str, dict] = {}
        for _, row in edited.iterrows():
            lid = row["ID"]
            old_status = pipeline_data.get(lid, {}).get("status", "Prospect")
            new_status = row["Status"]
            log_pipeline_change(lid, old_status, new_status)
            new_pipeline[lid] = {
                "status":        new_status,
                "contact_name":  row["Agent"],
                "contact_phone": row["Contact phone"],
                "notes":         row["Notes"],
                "updated_at":    datetime.now(timezone.utc).isoformat(),
            }
        save_pipeline(new_pipeline)
        st.success(f"Pipeline saved — {len(new_pipeline)} listings tracked.")
        st.rerun()

    st.caption(
        "Status changes are logged to `data/pipeline_audit.jsonl` for history. "
        "Shows all scored listings regardless of active sidebar filters."
    )

# ══════════════════════════════════════════════════════════════════════════════
# MAP TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_map:
    if mapped_count == 0:
        st.markdown(
            "<div style='text-align:center;padding:40px;color:#888'>"
            "<div style='font-size:36px'>🗺️</div>"
            "<div style='font-size:16px;font-weight:600;margin-top:8px'>No listings with coordinates</div>"
            "<div style='margin-top:4px'>Run the scraper — addresses will be geocoded automatically.</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        # Score legend chips
        lcols = st.columns(5)
        for col, (label, color) in zip(lcols, [
            ("≥70", "#27ae60"), ("50–70", "#f39c12"),
            ("30–50", "#e67e22"), ("<30", "#e74c3c"), ("No score", "#95a5a6"),
        ]):
            col.markdown(
                f"<span style='background:{color};padding:3px 10px;border-radius:12px;"
                f"color:white;font-size:12px;font-weight:600'>{label}</span>",
                unsafe_allow_html=True,
            )

        cluster_note = " · clustered" if _HAS_CLUSTER else ""
        map_view_note = " · 📍 filtered to view" if (filter_by_map and "map_bounds" in st.session_state) else ""
        st.caption(
            f"**{mapped_count}** of {len(filtered)} listings have coordinates{cluster_note}{map_view_note}. "
            "Click a pin for details."
        )

        m, _ = build_map(filtered, show_corridors, show_peza)
        map_result = st_folium(m, width=None, height=600, returned_objects=["bounds"], key="main_map")

        if map_result and map_result.get("bounds"):
            st.session_state.map_bounds = map_result["bounds"]

        if filter_by_map and "map_bounds" in st.session_state:
            b = st.session_state.map_bounds
            sw2, ne2 = b.get("_southWest", {}), b.get("_northEast", {})
            st.caption(
                f"🗺️ Viewport filter active: "
                f"{sw2.get('lat','?'):.3f}°–{ne2.get('lat','?'):.3f}°N, "
                f"{sw2.get('lng','?'):.3f}°–{ne2.get('lng','?'):.3f}°E"
            )

# ══════════════════════════════════════════════════════════════════════════════
# TABLE TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_table:
    df = listings_to_df(filtered)

    def fmt(v, fmt_str, fallback="—"):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return fallback
        try:
            return fmt_str.format(v)
        except Exception:
            return fallback

    display_df = pd.DataFrame({
        "New":       df["New"],
        "Score":     df["Score"].apply(lambda v: fmt(v, "{:.0f}")),
        "Title":     df["Title"],
        "Region":    df["Region"],
        "sqm":       df["sqm"].apply(lambda v: fmt(v, "{:,.0f}")),
        "Docks":     df["Docks"].apply(lambda v: fmt(v, "{:.0f}")),
        "Height m":  df["Height m"].apply(lambda v: fmt(v, "{:.1f}")),
        "SLEX km":   df["SLEX km"].apply(lambda v: fmt(v, "{:.1f}")),
        "NLEX km":   df["NLEX km"].apply(lambda v: fmt(v, "{:.1f}")),
        "C5 km":     df["C5 km"].apply(lambda v: fmt(v, "{:.1f}")),
        "PEZA km":   df["PEZA km"].apply(lambda v: fmt(v, "{:.1f}")),
        "Flood":     df["Flood"],
        "Price PHP": df["Price PHP"].apply(lambda v: fmt(v, "{:,.0f}")),
        "Agent":     df["Agent"],
        "Missing":   df["Missing"],
        "Dup":       df["Dup"],
        "Source":    df["Source"],
        "Link":      df["Link"],
    })

    # Score column: background colour tint per row using pandas Styler
    def _style_row(row):
        score_val = df.loc[row.name, "Score"] if row.name < len(df) else None
        is_new_row = df.loc[row.name, "New"] == "🆕" if row.name < len(df) else False
        styles = [""] * len(row)
        if is_new_row:
            styles = ["background-color:#fffde7"] * len(row)
        if score_val is not None and not pd.isna(score_val):
            c = score_color(float(score_val))
            r_int, g_int, b_int = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
            score_idx = list(display_df.columns).index("Score")
            styles[score_idx] = f"background-color:rgba({r_int},{g_int},{b_int},0.18);font-weight:700"
        return styles

    styled = display_df.style.apply(_style_row, axis=1)

    st.dataframe(
        styled,
        column_config={
            "New":   st.column_config.TextColumn("New", width="small"),
            "Link":  st.column_config.LinkColumn("Link"),
            "Score": st.column_config.TextColumn("Score (/100)"),
        },
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("📈 Summary stats"):
        c1, c2, c3, c4 = st.columns(4)
        has_sqm   = [l.listing.sqm for l in filtered if l.listing.sqm]
        has_docks = [l.listing.dock_doors for l in filtered if l.listing.dock_doors is not None]
        has_price = [l.listing.price_php for l in filtered if l.listing.price_php]
        has_slex  = [l.enriched.corridor_distances_km.get("SLEX")
                     for l in filtered
                     if l.enriched.corridor_distances_km and l.enriched.corridor_distances_km.get("SLEX")]
        c1.metric("Avg sqm",       f"{sum(has_sqm)/len(has_sqm):,.0f}" if has_sqm else "—")
        c2.metric("Avg docks",     f"{sum(has_docks)/len(has_docks):.1f}" if has_docks else "—")
        c3.metric("Avg price PHP", f"{sum(has_price)/len(has_price):,.0f}" if has_price else "—")
        c4.metric("Avg SLEX km",   f"{sum(has_slex)/len(has_slex):.1f}" if has_slex else "—")
        st.caption(
            f"sqm data: {len(has_sqm)}/{len(filtered)}  |  "
            f"dock data: {len(has_docks)}/{len(filtered)}  |  "
            f"price data: {len(has_price)}/{len(filtered)}"
        )

# ══════════════════════════════════════════════════════════════════════════════
# SCORE BREAKDOWN TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_breakdown:
    scored_shown = [l for l in filtered if l.score is not None and l.score_breakdown]
    if not scored_shown:
        st.markdown(
            "<div style='text-align:center;padding:40px;color:#888'>"
            "<div style='font-size:36px'>📈</div>"
            "<div style='font-size:16px;font-weight:600;margin-top:8px'>No scored listings in this filter</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"Top {min(10, len(scored_shown))} scored listings — each field shows points earned vs. max weight.")
        for l in scored_shown[:10]:
            b = l.score_breakdown
            color = score_color(l.score)

            with st.container():
                title_col, _ = st.columns([4, 1])
                title_col.markdown(
                    f"**{l.listing.title[:60] or l.id}**  "
                    f"<span style='color:#888;font-size:12px'>{l.listing.region or '?'}</span>",
                    unsafe_allow_html=True,
                )

                fields = [
                    ("Floor area (sqm)",   b.sqm,            spec.weights.sqm),
                    ("Dock doors",         b.dock_doors,      spec.weights.dock_doors),
                    ("Clear height",       b.clear_height_m,  spec.weights.clear_height_m),
                    ("Region match",       b.region,          spec.weights.region),
                    ("Corridor access",    b.corridor_access, spec.weights.corridor_access),
                    ("PEZA proximity",     b.peza_zone,       spec.weights.peza_zone),
                    ("Flood risk",         b.max_flood_risk,  spec.weights.max_flood_risk),
                ]
                for fname, fval, fmax in fields:
                    if fmax <= 0:
                        continue
                    pct = min(fval / fmax, 1.0) if fmax else 0.0
                    fc, _r = st.columns([3, 1])
                    fc.caption(fname)
                    fc.progress(pct)
                    _r.markdown(
                        f"<div style='text-align:right;padding-top:18px;"
                        f"font-size:13px;color:#555'>{fval:.0f}/{fmax:.0f}</div>",
                        unsafe_allow_html=True,
                    )

                # Total score badge at bottom
                st.markdown(
                    f"<div style='text-align:right;margin-top:4px'>"
                    f"<span style='background:{color};color:white;border-radius:20px;"
                    f"padding:4px 16px;font-weight:800;font-size:18px'>"
                    f"Total: {l.score:.0f}/100</span></div>",
                    unsafe_allow_html=True,
                )
                st.divider()
