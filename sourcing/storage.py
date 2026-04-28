"""
JSON flat-file storage for Phase 1.

Directory layout:
  data/raw/          — one file per listing: {id}.json  (RawListing)
  data/scored/       — current.json (list of ScoredListing, written after each score run)
  data/status.json   — scraper progress (ScraperStatus)

Phase 2 migrates this to Postgres. The interface is designed so the scorer
and dashboard can swap implementations without changing callers.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sourcing.models import RawListing, ScoredListing, ScraperStatus

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
SCORED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "scored")
STATUS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "status.json")

STALE_DAYS = 7
DEDUP_DISTANCE_THRESHOLD_KM = 0.1   # 100m
DEDUP_SQFT_PCT_THRESHOLD = 0.05     # 5%


# ── Raw listings ──────────────────────────────────────────────────────────────

def save_raw(listing: RawListing, base_dir: str = RAW_DIR) -> None:
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, f"{listing.id}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(listing.model_dump_json(indent=2))


def load_raw(listing_id: str, base_dir: str = RAW_DIR) -> Optional[RawListing]:
    path = os.path.join(base_dir, f"{listing_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return RawListing.model_validate_json(f.read())


def load_all_raw(base_dir: str = RAW_DIR) -> List[RawListing]:
    if not os.path.exists(base_dir):
        return []
    listings = []
    for fname in os.listdir(base_dir):
        if fname.endswith(".json"):
            path = os.path.join(base_dir, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    listings.append(RawListing.model_validate_json(f.read()))
            except Exception:
                continue  # skip corrupted files
    return listings


def mark_stale(base_dir: str = RAW_DIR) -> int:
    """
    Mark listings older than STALE_DAYS as 'stale'.
    Returns count of listings marked stale.
    """
    now = datetime.now(timezone.utc)
    count = 0
    for listing in load_all_raw(base_dir):
        if listing.status == "active":
            age = (now - listing.scraped_at).days
            if age >= STALE_DAYS:
                listing.status = "stale"
                save_raw(listing, base_dir)
                count += 1
    return count


def get_stale_ids(base_dir: str = RAW_DIR) -> List[str]:
    """Return IDs of all stale listings (to re-scrape first)."""
    return [l.id for l in load_all_raw(base_dir) if l.status == "stale"]


# ── Scored listings ───────────────────────────────────────────────────────────

def save_scored(listings: List[ScoredListing], base_dir: str = SCORED_DIR) -> None:
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, "current.json")
    data = [l.model_dump(mode="json") for l in listings]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def load_scored(base_dir: str = SCORED_DIR) -> List[ScoredListing]:
    path = os.path.join(base_dir, "current.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [ScoredListing.model_validate(item) for item in raw]


# ── Scraper status ────────────────────────────────────────────────────────────

def save_status(status: ScraperStatus, path: str = STATUS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(status.model_dump_json(indent=2))


def load_status(path: str = STATUS_PATH) -> ScraperStatus:
    if not os.path.exists(path):
        return ScraperStatus()
    try:
        with open(path, encoding="utf-8") as f:
            return ScraperStatus.model_validate_json(f.read())
    except Exception:
        return ScraperStatus(state="error", message="status.json corrupted — reset")


def reset_status(path: str = STATUS_PATH) -> None:
    save_status(ScraperStatus(state="idle", message="Reset by dashboard"), path)


# ── Deduplication ─────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def flag_duplicates(listings: List[ScoredListing]) -> List[ScoredListing]:
    """
    Cross-source duplicate detection.
    Flags a listing as "possible_duplicate_of" if:
      - lat/lng within 100m AND
      - sqft within ±5%
    Does NOT auto-merge. Returns the same list with flags set.
    """
    for i, a in enumerate(listings):
        if a.possible_duplicate_of:
            continue
        a_lat = a.listing.lat
        a_lng = a.listing.lng
        a_sqft = a.listing.sqft

        for j, b in enumerate(listings):
            if i >= j:
                continue
            if b.possible_duplicate_of:
                continue
            if a.source == b.source:
                continue  # only cross-source dedup

            b_lat = b.listing.lat
            b_lng = b.listing.lng
            b_sqft = b.listing.sqft

            # Check lat/lng proximity
            if a_lat is None or a_lng is None or b_lat is None or b_lng is None:
                continue
            dist_km = _haversine_km(a_lat, a_lng, b_lat, b_lng)
            if dist_km > DEDUP_DISTANCE_THRESHOLD_KM:
                continue

            # Check sqft similarity
            if a_sqft is None or b_sqft is None:
                continue
            sqft_diff_pct = abs(a_sqft - b_sqft) / max(a_sqft, b_sqft)
            if sqft_diff_pct > DEDUP_SQFT_PCT_THRESHOLD:
                continue

            # Both conditions met — flag the lower-scored one
            if a.score >= b.score:
                b.possible_duplicate_of = a.id
            else:
                a.possible_duplicate_of = b.id

    return listings
