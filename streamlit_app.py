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
from sourcing.proposal import generate_proposal_pdf
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
from sourcing.search import (
    apply_nl_filters,
    cached_parse_nl,
    load_scored_cached,
    sidebar_filter,
)

_HAS_ANTHROPIC = bool(os.environ.get("ANTHROPIC_API_KEY", ""))

SPEC_PATH      = os.path.join(ROOT, "spec.yaml")
DATA_DIR       = os.path.join(ROOT, "data")
PIPELINE_PATH  = os.path.join(DATA_DIR, "pipeline.json")
AUDIT_PATH     = os.path.join(DATA_DIR, "pipeline_audit.jsonl")
SCRAPER_CMD    = [sys.executable, "-m", "sourcing.scrapers.orchestrator", "--spec", SPEC_PATH]
POLL_INTERVAL_S = 3

GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO    = "aarondelmo-spx/site-sourcing"
WORKFLOW_FILE  = "scrape.yml"

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


@st.cache_data(show_spinner=False)
def listings_to_df(
    _cache_key: tuple,          # (id, score, is_new) tuples — drives invalidation
    _listings: list,            # underscore prefix = skipped by Streamlit's hasher
) -> pd.DataFrame:
    """DataFrame conversion — cached by listing IDs+scores, rebuilt only when filtered set changes."""
    rows = []
    for l in _listings:
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

    # ── Remote trigger via GitHub Actions ─────────────────────────────────────
    if GITHUB_TOKEN:
        if st.button("☁️ Run Scraper (Cloud)", help="Triggers GitHub Actions — scrapes on GitHub's servers. Takes ~30 min. No local PC needed."):
            import requests as _req
            resp = _req.post(
                f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
                headers={
                    "Authorization": f"Bearer {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={"ref": "master"},
            )
            if resp.status_code == 204:
                st.success("✅ Cloud scrape triggered! Data will refresh in ~30 minutes.")
            else:
                st.error(f"Failed to trigger: {resp.status_code} — {resp.text}")
    else:
        st.caption("💡 Set `GITHUB_TOKEN` secret to enable cloud scraping from this button.")


@st.fragment(run_every=POLL_INTERVAL_S if status.state == "running" else None)
def status_panel():
    s = load_status()
    if s.state == "running" and not is_pid_alive(s.pid):
        s.state = "error"
        s.message = "Scraper process ended -- possibly interrupted. Reset to run again."
        save_status(s)
        s = load_status()
    if s.state == "running":
        # ── Progress bar ──────────────────────────────────────────────────────
        fetched  = s.fetched or 0
        total    = s.total   or 0
        progress = min(fetched / total, 0.99) if total > 0 else 0.0

        # Elapsed time
        elapsed_str = ""
        if s.started_at:
            try:
                started = datetime.fromisoformat(s.started_at.replace("Z", "+00:00"))
                elapsed = datetime.now(timezone.utc) - started
                mins, secs = divmod(int(elapsed.total_seconds()), 60)
                elapsed_str = f" · ⏱ {mins}m {secs}s elapsed"
            except Exception:
                pass

        st.info(f"⏳ **Scraper running**{elapsed_str}")
        st.progress(
            progress,
            text=f"**{fetched:,}** listings fetched · {s.message}"
        )

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
all_scored = load_scored_cached(os.path.join(DATA_DIR, "scored"))

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

filtered = sidebar_filter(
    all_scored,
    region_filter=region_filter,
    sqm_range=sqm_range,
    dock_min=dock_min,
    height_min=height_min,
    slex_max_km=slex_max_km,
    price_max=price_max_filter,
    show_duplicates=show_duplicates,
    show_incomplete=show_incomplete,
    include_unknown_sqm=include_unknown_sqm,
    include_unknown_docks=include_unknown_docks,
    include_unknown_height=include_unknown_height,
    include_unknown_slex=include_unknown_slex,
    include_unknown_price=include_unknown_price,
)

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

# ── NL search helpers (apply_nl_filters imported from sourcing.search) ────────

def _search_card_html(l: ScoredListing, rank: int) -> str:
    """Return a clean HTML property card for the search results list."""
    score      = l.score if l.score is not None else 0
    color      = score_color(score)
    score_str  = f"{score:.0f}" if l.score is not None else "—"
    title      = _html.escape(l.listing.title[:80] or "(no title)")
    addr       = _html.escape(l.listing.address or l.listing.region or "")
    region     = _html.escape(l.listing.region or "?")
    sqm_str    = f"{l.listing.sqm:,.0f} sqm" if l.listing.sqm else "—"
    docks_str  = str(l.listing.dock_doors) if l.listing.dock_doors is not None else "—"
    height_str = f"{l.listing.clear_height_m:.1f} m" if l.listing.clear_height_m else "—"
    flood_str  = (l.enriched.flood_risk or "?").capitalize()
    corridors  = l.enriched.corridor_distances_km or {}
    slex_str   = f"{corridors['SLEX']:.1f} km" if corridors.get("SLEX") else "—"
    price_str  = f"₱{l.listing.price_php:,.0f}/mo" if l.listing.price_php else "—"
    url        = _html.escape(l.url)
    agent      = _html.escape(l.listing.agent_name or "")

    flood_color = "#e74c3c" if flood_str.lower() == "high" else (
        "#f39c12" if flood_str.lower() == "medium" else "#27ae60"
    )

    missing_html = ""
    if l.missing_required:
        missing_html = (
            f"<div style='margin-top:6px;font-size:11px;color:#e74c3c'>"
            f"⚠ Missing: {_html.escape(', '.join(l.missing_required))}</div>"
        )

    return f"""
<div style='background:white;border:1px solid #e8e8e8;border-radius:10px;
            padding:16px 18px;margin-bottom:12px;
            box-shadow:0 1px 4px rgba(0,0,0,.07);font-family:sans-serif'>
  <div style='display:flex;align-items:flex-start;gap:12px'>
    <div style='flex-shrink:0;text-align:center'>
      <div style='color:#bbb;font-size:11px;font-weight:600'>#{rank}</div>
      <div style='background:{color};color:white;border-radius:20px;
                  padding:4px 12px;font-weight:800;font-size:20px;
                  line-height:1.2;margin-top:2px'>{score_str}</div>
      <div style='color:#bbb;font-size:10px;margin-top:1px'>/100</div>
    </div>
    <div style='flex:1;min-width:0'>
      <div style='font-size:14px;font-weight:700;line-height:1.35;
                  color:#1a1a1a;margin-bottom:3px'>{title}</div>
      <div style='font-size:12px;color:#888;margin-bottom:10px'>{addr}</div>
      <div style='display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px'>
        <span><b>📐</b> {sqm_str}</span>
        <span><b>🚪</b> {docks_str} docks</span>
        <span><b>↕</b> {height_str} ceiling</span>
        <span><b>🛣️ SLEX</b> {slex_str}</span>
        <span><b>💰</b> {price_str}</span>
        <span style='color:{flood_color}'><b>🌊</b> {flood_str} flood</span>
      </div>
      {missing_html}
      <div style='margin-top:10px;display:flex;align-items:center;justify-content:space-between'>
        <span style='font-size:11px;color:#aaa'>{region}{(" · " + agent) if agent else ""}</span>
        <a href='{url}' target='_blank'
           style='background:#EE4D2D;color:white;border-radius:6px;
                  padding:5px 12px;font-size:12px;font-weight:600;
                  text-decoration:none;white-space:nowrap'>View listing →</a>
      </div>
    </div>
  </div>
</div>"""


def generate_search_html(
    listings: List[ScoredListing],
    query: str,
    parsed: dict,
) -> str:
    """Generate a self-contained standalone HTML page with Leaflet map + property cards."""
    # Build GeoJSON for Leaflet
    features = []
    for i, l in enumerate(listings):
        if l.listing.lat is None or l.listing.lng is None:
            continue
        color = score_color(l.score)
        score = l.score or 0
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [l.listing.lng, l.listing.lat]},
            "properties": {
                "rank":    i + 1,
                "title":   l.listing.title or "(no title)",
                "score":   f"{score:.0f}",
                "region":  l.listing.region or "?",
                "sqm":     f"{l.listing.sqm:,.0f} sqm" if l.listing.sqm else "—",
                "price":   f"₱{l.listing.price_php:,.0f}/mo" if l.listing.price_php else "—",
                "url":     l.url,
                "color":   color,
            },
        })

    geojson = json.dumps({"type": "FeatureCollection", "features": features})

    cards_html = "".join(_search_card_html(l, i + 1) for i, l in enumerate(listings[:20]))

    # Filter summary
    parts = []
    if parsed.get("sqm_min") or parsed.get("sqm_max"):
        lo = parsed.get("sqm_min", 0)
        hi = parsed.get("sqm_max", 0)
        parts.append(f"{lo:,.0f}–{hi:,.0f} sqm" if lo and hi else
                     (f"≥{lo:,.0f} sqm" if lo else f"≤{hi:,.0f} sqm"))
    if parsed.get("region_priority"):
        parts.append(", ".join(parsed["region_priority"]))
    if parsed.get("budget_max_sqm_month"):
        parts.append(f"≤₱{parsed['budget_max_sqm_month']:,.0f}/sqm/mo")
    if parsed.get("dock_doors_min"):
        parts.append(f"≥{parsed['dock_doors_min']} docks")
    filter_summary = "  ·  ".join(parts) if parts else "No specific filters applied"

    today = datetime.now().strftime("%B %d, %Y")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Warehouse Search — SPX Site Sourcing</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #f5f5f7; color: #1a1a1a; }}
header {{ background: #EE4D2D; color: white; padding: 16px 24px; }}
header h1 {{ font-size: 20px; font-weight: 800; }}
header p  {{ font-size: 13px; opacity: .85; margin-top: 3px; }}
.query-box {{ background: white; border-left: 4px solid #EE4D2D;
              padding: 12px 20px; margin: 16px 24px; border-radius: 6px;
              font-size: 13px; color: #444; }}
.query-box strong {{ color: #EE4D2D; }}
.filter-chips {{ padding: 0 24px 10px; font-size: 12px; color: #888; }}
.layout {{ display: flex; gap: 0; height: calc(100vh - 160px); min-height: 500px; }}
#map {{ flex: 0 0 55%; border-right: 1px solid #ddd; }}
.cards-panel {{ flex: 1; overflow-y: auto; padding: 16px; background: #f5f5f7; }}
.cards-panel h2 {{ font-size: 14px; color: #888; margin-bottom: 12px; font-weight: 500; }}
.card {{ background: white; border: 1px solid #e8e8e8; border-radius: 10px;
         padding: 14px 16px; margin-bottom: 10px;
         box-shadow: 0 1px 4px rgba(0,0,0,.06); }}
.card-header {{ display: flex; gap: 12px; align-items: flex-start; }}
.score-badge {{ flex-shrink: 0; text-align: center; }}
.score-badge .num {{ border-radius: 20px; padding: 3px 11px;
                      font-weight: 800; font-size: 18px; color: white; }}
.score-badge .label {{ font-size: 10px; color: #bbb; margin-top: 2px; }}
.card-body h3 {{ font-size: 13px; font-weight: 700; line-height: 1.3; margin-bottom: 3px; }}
.card-body .addr {{ font-size: 11px; color: #888; margin-bottom: 8px; }}
.specs {{ display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: #555; }}
.card-footer {{ margin-top: 10px; display: flex; justify-content: space-between;
                align-items: center; }}
.card-footer .meta {{ font-size: 11px; color: #aaa; }}
.card-footer a {{ background: #EE4D2D; color: white; border-radius: 5px;
                  padding: 4px 10px; font-size: 11px; font-weight: 600;
                  text-decoration: none; }}
.leaflet-popup-content-wrapper {{ border-radius: 10px; box-shadow: 0 2px 12px rgba(0,0,0,.15); }}
.leaflet-popup-content {{ margin: 0; }}
.pop {{ padding: 12px; font-family: sans-serif; width: 220px; }}
.pop-score {{ display:inline-block;border-radius:20px;padding:2px 10px;
              color:white;font-weight:800;font-size:15px;margin-bottom:6px; }}
.pop-title {{ font-size:12px;font-weight:700;line-height:1.3;margin-bottom:4px; }}
.pop-specs {{ font-size:11px;color:#555;margin-bottom:8px; }}
.pop-link {{ display:block;background:#EE4D2D;color:white;text-align:center;
             border-radius:5px;padding:5px;font-size:11px;font-weight:600;
             text-decoration:none; }}
footer {{ text-align:center;padding:16px;font-size:11px;color:#aaa; }}
@media(max-width:700px){{
  .layout{{flex-direction:column;height:auto;}}
  #map{{flex:none;height:350px;}}
}}
</style>
</head>
<body>
<header>
  <h1>🏭 SPX Site Sourcing — Warehouse Search</h1>
  <p>Generated {today}</p>
</header>
<div class="query-box">
  <strong>Search:</strong> {_html.escape(query)}
</div>
<div class="filter-chips">Filters applied: {_html.escape(filter_summary)} &nbsp;·&nbsp; {len(listings)} results</div>
<div class="layout">
  <div id="map"></div>
  <div class="cards-panel">
    <h2>{len(listings)} properties found</h2>
    {"".join(_search_card_html(l, i+1) for i, l in enumerate(listings[:20]))}
    {"<p style='text-align:center;color:#aaa;font-size:12px;padding:10px'>Showing top 20 of " + str(len(listings)) + " results</p>" if len(listings) > 20 else ""}
  </div>
</div>
<footer>SPX Site Sourcing &nbsp;·&nbsp; Confidential &nbsp;·&nbsp; {today}</footer>
<script>
var map = L.map('map').setView([14.40, 121.00], 9);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png',{{
  attribution:'© OpenStreetMap, © CARTO', maxZoom:18
}}).addTo(map);
var data = {geojson};
L.geoJSON(data, {{
  pointToLayer: function(f, latlng) {{
    var p = f.properties;
    return L.circleMarker(latlng, {{
      radius: Math.min(14, 6 + parseInt(p.score)/12),
      fillColor: p.color, color: 'white',
      weight: 2, opacity: 1, fillOpacity: 0.88
    }});
  }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindPopup(
      '<div class="pop"><span class="pop-score" style="background:'+p.color+'">'
      +p.score+'</span>'
      +'<div class="pop-title">#'+p.rank+' '+p.title+'</div>'
      +'<div class="pop-specs">'+p.sqm+' &nbsp;·&nbsp; '+p.price
      +' &nbsp;·&nbsp; '+p.region+'</div>'
      +'<a class="pop-link" href="'+p.url+'" target="_blank">View listing →</a></div>',
      {{maxWidth: 250}}
    );
    layer.bindTooltip('#'+p.rank+' '+p.title.substring(0,40), {{sticky:false}});
  }}
}}).addTo(map);
</script>
</body>
</html>"""


# ── View tabs  (Search first, then ops tabs) ───────────────────────────────────

tab_search, tab_pipeline, tab_map, tab_table, tab_breakdown = st.tabs(
    ["🔍 Search", "🏗️ Pipeline", "🗺️ Map", "📋 Table", "📈 Score breakdown"]
)

# ══════════════════════════════════════════════════════════════════════════════
# SEARCH TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_search:
    # ── Hero search bar ───────────────────────────────────────────────────────
    st.markdown(
        "<div style='padding:24px 0 16px'>"
        "<div style='font-size:26px;font-weight:800;color:#1a1a1a;margin-bottom:6px'>"
        "🔍 Find a warehouse</div>"
        "<div style='font-size:14px;color:#888'>"
        "Describe what you need in plain language — area, location, price, features.</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    _sq_col, _btn_col = st.columns([6, 1])
    with _sq_col:
        _search_query = st.text_input(
            "search_bar",
            placeholder=(
                "e.g.  10,000 sqm warehouse in Laguna under ₱200/sqm, 6 docks, near SLEX"
            ),
            label_visibility="collapsed",
            key="search_nl_query",
        )
    with _btn_col:
        _search_btn = st.button("Search", type="primary", use_container_width=True, key="search_go")

    # ── Run search ────────────────────────────────────────────────────────────
    _q_stripped = _search_query.strip()
    # Double-submit guard: skip if query unchanged since last successful search
    _last_q = st.session_state.get("_search_query", "")
    _should_search = (
        _search_btn
        and _q_stripped
        and not st.session_state.get("_search_locked", False)
        and (_q_stripped != _last_q or st.session_state.get("_search_results") is None)
    )
    if _should_search:
        if not _HAS_ANTHROPIC:
            st.warning("Set `ANTHROPIC_API_KEY` to enable AI-powered search.")
        else:
            st.session_state["_search_locked"] = True
            with st.spinner("Parsing your search…"):
                # cached_parse_nl never calls Claude twice for the same text
                _s_parsed, _s_err = cached_parse_nl(_q_stripped)
            st.session_state["_search_locked"] = False
            if _s_err:
                st.warning(f"Couldn't fully parse: {_s_err}")
            _s_results = apply_nl_filters(all_scored, _s_parsed)
            st.session_state["_search_results"]  = _s_results
            st.session_state["_search_query"]    = _q_stripped
            st.session_state["_search_parsed"]   = _s_parsed

    _sr = st.session_state.get("_search_results")
    _sq = st.session_state.get("_search_query", "")
    _sp = st.session_state.get("_search_parsed", {})

    if not _HAS_ANTHROPIC and not _sr:
        st.info(
            "**ANTHROPIC_API_KEY not set** — search uses AI to understand natural language.  \n"
            "Set the key to enable it, or use the sidebar filters on the Map/Table tabs."
        )
    elif _sr is None:
        # No search yet — show quick-start hints
        st.markdown(
            "<div style='padding:40px 0;text-align:center;color:#aaa'>"
            "<div style='font-size:40px;margin-bottom:12px'>🏭</div>"
            "<div style='font-size:15px;font-weight:600;color:#888'>Try a search above</div>"
            "<div style='font-size:13px;margin-top:8px'>Examples:<br>"
            "• <i>8,000 sqm dry warehouse in Laguna, 4+ docks, low flood risk</i><br>"
            "• <i>Grade A warehouse near SLEX under PHP 180/sqm Laguna or Cavite</i><br>"
            "• <i>10,000 to 15,000 sqm with genset, PEZA zone preferred</i></div>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        # ── Filter chips (what was applied) ───────────────────────────────────
        _chip_parts = []
        if _sp.get("sqm_min") or _sp.get("sqm_max"):
            lo, hi = _sp.get("sqm_min", 0), _sp.get("sqm_max", 0)
            _chip_parts.append(
                f"📐 {lo:,.0f}–{hi:,.0f} sqm" if (lo and hi) else
                (f"📐 ≥{lo:,.0f} sqm" if lo else f"📐 ≤{hi:,.0f} sqm")
            )
        if _sp.get("region_priority"):
            _chip_parts.append("📍 " + ", ".join(_sp["region_priority"]))
        if _sp.get("budget_max_sqm_month"):
            _chip_parts.append(f"💰 ≤₱{_sp['budget_max_sqm_month']:,.0f}/sqm/mo")
        if _sp.get("dock_doors_min"):
            _chip_parts.append(f"🚪 ≥{_sp['dock_doors_min']} docks")
        if _sp.get("clear_height_min"):
            _chip_parts.append(f"↕ ≥{_sp['clear_height_min']:.1f}m ceiling")
        if (_sp.get("slex_max_km") or 60) < 60:
            _chip_parts.append(f"🛣️ ≤{_sp['slex_max_km']:.0f}km SLEX")
        if _sp.get("peza_required"):
            _chip_parts.append("🏭 PEZA zone")

        _chip_html = "".join(
            f"<span style='background:#f0f0f0;border-radius:20px;padding:3px 10px;"
            f"font-size:12px;margin-right:6px;color:#444'>{c}</span>"
            for c in _chip_parts
        )
        if _chip_parts:
            st.markdown(
                f"<div style='margin-bottom:10px'>{_chip_html}</div>",
                unsafe_allow_html=True,
            )

        # ── Results count + export button ─────────────────────────────────────
        _r_col1, _r_col2 = st.columns([4, 1])
        _r_col1.markdown(
            f"**{len(_sr)}** properties found"
            + (f"  ·  showing top {min(len(_sr), 20)}" if len(_sr) > 20 else "")
        )
        if _sr:
            _html_bytes = generate_search_html(_sr, _sq, _sp).encode("utf-8")
            _r_col2.download_button(
                "⬇️ Export HTML",
                data=_html_bytes,
                file_name=f"SPX_Search_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                mime="text/html",
                key="export_search_html",
                use_container_width=True,
            )

        if not _sr:
            st.markdown(
                "<div style='text-align:center;padding:40px;color:#888'>"
                "<div style='font-size:32px'>🔍</div>"
                "<div style='font-size:15px;font-weight:600;margin-top:8px'>"
                "No listings match your search</div>"
                "<div style='margin-top:4px;font-size:13px'>"
                "Try broader criteria — e.g. remove a region constraint or increase budget.</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            # ── Map + cards layout ─────────────────────────────────────────────
            _map_col, _card_col = st.columns([3, 2], gap="medium")

            with _map_col:
                _sm, _smapped = build_map(_sr[:50], show_corridors=True, show_peza=False)
                st_folium(_sm, width=None, height=520, returned_objects=[], key="search_map")
                if _smapped == 0:
                    st.caption("No coordinates available for these listings yet.")

            with _card_col:
                st.markdown(
                    "<div style='height:520px;overflow-y:auto;padding-right:4px'>",
                    unsafe_allow_html=True,
                )
                for _i, _l in enumerate(_sr[:20]):
                    st.markdown(_search_card_html(_l, _i + 1), unsafe_allow_html=True)
                if len(_sr) > 20:
                    st.caption(f"Showing top 20 of {len(_sr)} results. Export HTML to see all.")
                st.markdown("</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE TAB
# ══════════════════════════════════════════════════════════════════════════════

with tab_pipeline:
    pipeline_data = load_pipeline()

    # ── Build rows from ALL scored listings (for global counts + safe save) ──
    all_pipeline_rows = []
    for l in all_scored:
        p = pipeline_data.get(l.id, {})
        all_pipeline_rows.append({
            "ID":            l.id,
            "Status":        p.get("status", "Prospect"),
            "Title":         (l.listing.title[:55] or "(no title)"),
            "Region":        l.listing.region or "?",
            "sqm":           l.listing.sqm,          # numeric → sorts correctly
            "Score":         l.score,                 # numeric → sorts correctly
            "Agent":         l.listing.agent_name or p.get("contact_name", ""),
            "Contact phone": p.get("contact_phone", ""),
            "Notes":         p.get("notes", ""),
            "Link":          l.url,
        })
    all_pipeline_df = pd.DataFrame(all_pipeline_rows)

    # ── Determine which rows to show (respects sidebar filter) ───────────────
    using_filtered = len(filtered) < len(all_scored)
    filtered_ids = {l.id for l in filtered}

    if using_filtered:
        # Show only filtered rows, sorted by score desc (same as rest of UI)
        show_df = all_pipeline_df[all_pipeline_df["ID"].isin(filtered_ids)].copy()
        show_df = show_df.sort_values("Score", ascending=False, na_position="last")
        show_df = show_df.reset_index(drop=True)
        st.caption(
            f"Showing **{len(show_df)}** filtered listings · "
            f"{len(all_pipeline_df)} total scraped · "
            "**Clear all filters** in the sidebar to see everything."
        )
    else:
        show_df = all_pipeline_df

    # ── Status count pills (always reflect filtered view) ─────────────────────
    _count_df = show_df if using_filtered else all_pipeline_df
    display_counts = _count_df["Status"].value_counts()
    global_counts  = all_pipeline_df["Status"].value_counts()

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

    # Editable table — shows filtered view
    edited = st.data_editor(
        show_df,
        column_config={
            "ID":            st.column_config.TextColumn("ID", disabled=True, width="small"),
            "Status":        st.column_config.SelectboxColumn(
                "Status", options=PIPELINE_STATUSES, required=True, width="medium"
            ),
            "Title":         st.column_config.TextColumn("Title", disabled=True),
            "Region":        st.column_config.TextColumn("Region", disabled=True, width="small"),
            "sqm":           st.column_config.NumberColumn("sqm", format="%,.0f", disabled=True, width="small"),
            "Score":         st.column_config.NumberColumn("Score (/100)", format="%.0f", disabled=True, width="small"),
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
        # Start with ALL existing pipeline data — preserve non-visible rows
        new_pipeline: Dict[str, dict] = dict(pipeline_data)
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
        n_vis = len(edited)
        n_total = len(new_pipeline)
        st.success(
            f"Pipeline saved — {n_vis} visible listings updated"
            + (f", {n_total - n_vis} non-filtered listings preserved." if using_filtered else ".")
        )
        st.rerun()

    st.caption(
        "Status changes are logged to `data/pipeline_audit.jsonl` for history. "
        + ("Showing filtered view — clear sidebar filters to see all listings." if using_filtered
           else "Showing all scored listings.")
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
    _df_cache_key = tuple((l.id, l.score, l.is_new) for l in filtered)
    df = listings_to_df(_df_cache_key, filtered)

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

    # ── Proposal PDF generator ────────────────────────────────────────────────
    st.divider()
    with st.expander("📄 Generate Proposal PDF", expanded=False):
        st.caption(
            "Select listings to include, then generate a shareable PDF "
            "with comparison table and 3-year financial projections."
        )

        # Default selection: scored listings ≥ 60, capped at 5
        _default_ids = {
            l.id for l in sorted(
                [l for l in filtered if l.score is not None and l.score >= 60],
                key=lambda l: l.score, reverse=True,
            )[:5]
        }
        if not _default_ids:
            # Fallback: top 5 regardless of score
            _default_ids = {l.id for l in filtered[:5]}

        # Build options: "Score · Title (region)"
        _prop_options = {
            l.id: (
                f"{l.score:.0f} · " if l.score is not None else "— · "
            ) + f"{(l.listing.title or l.id)[:50]}  ({l.listing.region or '?'})"
            for l in filtered
        }

        _selected_ids = st.multiselect(
            "Listings to include (drag to reorder, max 8)",
            options=list(_prop_options.keys()),
            default=[i for i in _prop_options if i in _default_ids],
            format_func=lambda x: _prop_options.get(x, x),
            key="proposal_selection",
        )

        _req_for_pdf: Optional[ExpansionRequirement] = st.session_state.get("active_requirement")
        _proj_title = _req_for_pdf.project_name if _req_for_pdf else "Warehouse Shortlist"
        _proj_title = st.text_input("Proposal title", value=_proj_title, key="proposal_title")

        _gen_col, _dl_col = st.columns([2, 3])

        if _gen_col.button("📄 Generate PDF", type="primary",
                           disabled=len(_selected_ids) == 0,
                           key="gen_proposal_btn"):
            _shortlist = [l for l in filtered if l.id in _selected_ids]
            # Preserve order from multiselect
            _id_order = {lid: i for i, lid in enumerate(_selected_ids)}
            _shortlist.sort(key=lambda l: _id_order.get(l.id, 999))

            with st.spinner(f"Building PDF for {len(_shortlist)} listings…"):
                try:
                    _pdf_bytes = generate_proposal_pdf(
                        _shortlist,
                        requirement=_req_for_pdf,
                        project_name=_proj_title,
                    )
                    st.session_state["_proposal_pdf"] = _pdf_bytes
                    st.session_state["_proposal_title"] = _proj_title
                    st.success(f"PDF ready — {len(_pdf_bytes):,} bytes")
                except Exception as _e:
                    st.error(f"PDF generation failed: {_e}")

        _pdf_ready = st.session_state.get("_proposal_pdf")
        if _pdf_ready:
            from datetime import date as _date
            _fname = f"SPX_Proposal_{st.session_state.get('_proposal_title','Shortlist').replace(' ','_')}_{_date.today()}.pdf"
            _dl_col.download_button(
                label="⬇️ Download PDF",
                data=_pdf_ready,
                file_name=_fname,
                mime="application/pdf",
                key="download_proposal",
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
