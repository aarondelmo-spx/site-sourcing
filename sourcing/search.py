"""
sourcing/search.py
------------------
Pure, Streamlit-free functions for NL search and sidebar filtering.

Extracted from streamlit_app.py so they can be unit-tested without
importing Streamlit.  The app imports these instead of defining them inline.
"""
from __future__ import annotations

import functools
import json
import os
from typing import List, Optional, Tuple

from sourcing.models import ScoredListing
from sourcing.requirements import parse_requirement_nl
from sourcing.storage import load_scored as _load_scored_raw


# ── NL parse — thin cached wrapper ───────────────────────────────────────────

def _parse_requirement_nl_inner(text: str) -> Tuple[dict, Optional[str]]:
    """Thin shim so tests can monkeypatch without touching requirements.py."""
    return parse_requirement_nl(text)


@functools.lru_cache(maxsize=128)
def cached_parse_nl(text: str) -> Tuple[dict, Optional[str]]:
    """
    Call Claude to parse a natural-language requirement.

    Results are cached by exact text — identical queries never hit the API twice.
    Cache lives for the process lifetime (Streamlit session).  Call
    `cached_parse_nl.cache_clear()` in tests to reset between runs.
    """
    return _parse_requirement_nl_inner(text)


# ── load_scored — mtime-gated cache ──────────────────────────────────────────

_scored_cache: dict = {}   # {dir_path: (mtime, List[ScoredListing])}


def load_scored_cached(base_dir: str) -> List[ScoredListing]:
    """
    Load scored listings, re-reading disk only when current.json changes.

    Avoids Pydantic deserialization on every Streamlit rerun.
    Uses file mtime as the invalidation key — cheap OS stat, no hashing.
    """
    path = os.path.join(base_dir, "current.json")
    if not os.path.exists(path):
        return []

    mtime = os.path.getmtime(path)
    cached = _scored_cache.get(base_dir)
    if cached is not None and cached[0] == mtime:
        return cached[1]   # cache hit — same object

    # Cache miss — read and deserialise
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    listings = [ScoredListing.model_validate(item) for item in raw]
    _scored_cache[base_dir] = (mtime, listings)
    return listings


# ── apply_nl_filters — for the Search tab ────────────────────────────────────

def apply_nl_filters(
    listings: List[ScoredListing],
    parsed: dict,
) -> List[ScoredListing]:
    """
    Filter and sort listings against a parsed NL requirement dict.

    Unknown/missing field values (None) are never excluded — the caller can
    add explicit unknown-include toggles on top if needed.

    Confirmed duplicates (possible_duplicate_of is set) are always excluded;
    the search tab is for end-user consumption, not QA.
    """
    sqm_min  = float(parsed.get("sqm_min") or 0)
    sqm_max  = float(parsed.get("sqm_max") or 0)
    regions  = [r.strip() for r in (parsed.get("region_priority") or []) if r]
    budget   = float(parsed.get("budget_max_sqm_month") or 0)
    docks    = int(parsed.get("dock_doors_min") or 0)
    height   = float(parsed.get("clear_height_min") or 0)
    slex_km  = float(parsed.get("slex_max_km") or 60)
    peza     = bool(parsed.get("peza_required", False))

    # Pre-compute budget ceiling once (avoid per-listing multiply)
    budget_total: Optional[float] = (budget * sqm_max) if (budget > 0 and sqm_max > 0) else None

    def _passes(l: ScoredListing) -> bool:
        if l.possible_duplicate_of:
            return False
        lf = l.listing
        en = l.enriched

        # sqm
        if sqm_min > 0 and lf.sqm is not None and lf.sqm < sqm_min:
            return False
        if sqm_max > 0 and lf.sqm is not None and lf.sqm > sqm_max:
            return False
        # region
        if regions and lf.region not in regions:
            return False
        # budget
        if budget_total is not None and lf.price_php is not None and lf.price_php > budget_total:
            return False
        # docks
        if docks > 0 and lf.dock_doors is not None and lf.dock_doors < docks:
            return False
        # height
        if height > 0 and lf.clear_height_m is not None and lf.clear_height_m < height:
            return False
        # SLEX
        if slex_km < 60:
            d = (en.corridor_distances_km or {}).get("SLEX")
            if d is not None and d <= 200 and d > slex_km:
                return False
        # PEZA
        if peza:
            if en.peza_zone_km is None or en.peza_zone_km > 5:
                return False

        return True

    results = [l for l in listings if _passes(l)]
    results.sort(key=lambda l: l.score if l.score is not None else -1.0, reverse=True)
    return results


# ── sidebar_filter — single-pass replacement for the 7-step chain ─────────────

def sidebar_filter(
    listings: List[ScoredListing],
    region_filter: List[str],
    sqm_range: Optional[tuple],           # (lo, hi) or None for no limit
    dock_min: int,
    height_min: float,
    slex_max_km: float,
    price_max: Optional[float],
    show_duplicates: bool = False,
    show_incomplete: bool = True,
    include_unknown_sqm: bool = True,
    include_unknown_docks: bool = True,
    include_unknown_height: bool = True,
    include_unknown_slex: bool = True,
    include_unknown_price: bool = True,
) -> List[ScoredListing]:
    """
    Single-pass sidebar filter — replaces 7 sequential list comprehensions.

    All semantics are identical to the old multi-pass chain; the only
    difference is one iteration instead of seven.
    """
    sqm_lo = sqm_range[0] if sqm_range else 0
    sqm_hi = sqm_range[1] if sqm_range else None

    def _passes(l: ScoredListing) -> bool:
        # Incomplete / duplicate gates
        if not show_incomplete and l.missing_required:
            return False
        if not show_duplicates and l.possible_duplicate_of:
            return False

        # Region
        if region_filter and l.listing.region not in region_filter:
            return False

        # Floor area
        v_sqm = l.listing.sqm
        if v_sqm is None:
            if not include_unknown_sqm:
                return False
        else:
            if sqm_lo > 0 and v_sqm < sqm_lo:
                return False
            if sqm_hi is not None and v_sqm > sqm_hi:
                return False

        # Dock doors
        v_dock = l.listing.dock_doors
        if v_dock is None:
            if not include_unknown_docks:
                return False
        elif v_dock < dock_min:
            return False

        # Clear height
        v_ht = l.listing.clear_height_m
        if v_ht is None:
            if not include_unknown_height:
                return False
        elif v_ht < height_min:
            return False

        # SLEX distance
        if slex_max_km < 60:
            d = (l.enriched.corridor_distances_km or {}).get("SLEX")
            if d is None or d > 200:
                if not include_unknown_slex:
                    return False
            elif d > slex_max_km:
                return False

        # Price
        if price_max is not None:
            v_price = l.listing.price_php
            if v_price is None:
                if not include_unknown_price:
                    return False
            elif v_price > price_max:
                return False

        return True

    return [l for l in listings if _passes(l)]
