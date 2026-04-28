"""
Scoring engine — separate pass from scraping.

Takes raw JSON listings from data/raw/ and produces scored output in data/scored/.
Spec weights can be changed in spec.yaml and re-run without re-scraping.

Score = Σ (field_weight × field_match_pct), range 0–100.

Field match logic:
  sqft:            100% if in [min, max]; linear decay to 0 at ±20% of bounds; 0 beyond ±20%
  dock_doors:      100% if ≥ min; 0 if below
  clear_height_m:  100% if ≥ min; 0 if below
  region:          100% if in list; 0 if not
  corridor_access: % of required corridors within 5km (haversine)
  peza_zone:       100% if within threshold; 0 if not (skipped if peza_zone_within_km=null)
  max_flood_risk:  100% if ≤ max_risk; 0 if above

Missing required fields (sqft, dock_doors, region): listing scored separately as "incomplete".
Missing optional fields: treated as 0% for that field (penalizes missing data explicitly).
"""
from __future__ import annotations

import os
from typing import List, Optional

from sourcing.geocoding.corridor_distance import corridor_score_pct
from sourcing.models import (
    RawListing,
    ScoreBreakdown,
    ScoredListing,
    SpecConfig,
)
from sourcing.providers.base import FloodRiskProvider, PezaProvider
from sourcing.providers.csv_providers import CsvFloodRiskProvider, CsvPezaProvider
from sourcing.storage import (
    flag_duplicates,
    load_all_raw,
    save_scored,
)

CORRIDOR_THRESHOLD_KM = 5.0
SQFT_DECAY_BAND = 0.20   # 20% beyond min/max → score drops to 0


class ScoringEngine:
    """
    Phase 1 scoring engine.

    Usage:
        engine = ScoringEngine(spec)
        scored = engine.score_all()
        # results split into engine.complete and engine.incomplete
    """

    def __init__(
        self,
        spec: SpecConfig,
        flood_provider: Optional[FloodRiskProvider] = None,
        peza_provider: Optional[PezaProvider] = None,
        data_dir: str = "data",
    ):
        self.spec = spec
        self.data_dir = data_dir

        # Default to CSV providers if not injected (allows test injection)
        self.flood_provider: FloodRiskProvider = flood_provider or CsvFloodRiskProvider(
            os.path.join(data_dir, "ph-flood-risk.csv")
        )
        self.peza_provider: Optional[PezaProvider] = peza_provider
        if self.peza_provider is None and self.spec.peza_zone_within_km is not None:
            try:
                self.peza_provider = CsvPezaProvider(
                    os.path.join(data_dir, "peza_zones.csv")
                )
            except FileNotFoundError:
                print(
                    "[scorer] WARNING: peza_zones.csv not found — PEZA scoring disabled"
                )

    def score_all(self, raw_dir: Optional[str] = None) -> List[ScoredListing]:
        """
        Load all raw listings, score them, save to data/scored/current.json.
        Returns full list (complete + incomplete combined, sorted by score desc).
        """
        raw_dir = raw_dir or os.path.join(self.data_dir, "raw")
        raw_listings = load_all_raw(raw_dir)

        if not raw_listings:
            raise FileNotFoundError(
                "No raw listings found. Run the scraper first."
            )

        complete: List[ScoredListing] = []
        incomplete: List[ScoredListing] = []
        not_found: List[ScoredListing] = []

        for raw in raw_listings:
            scored = self._score_one(raw)
            if scored.status == "not_found":
                not_found.append(scored)
            elif raw.missing_required:
                incomplete.append(scored)
            else:
                complete.append(scored)

        # Sort complete by score descending
        complete.sort(key=lambda x: x.score, reverse=True)
        incomplete.sort(key=lambda x: x.score, reverse=True)

        # Cross-source dedup (complete listings only)
        complete = flag_duplicates(complete)

        # Merge for storage: complete first, then incomplete, then not_found
        all_scored = complete + incomplete + not_found

        save_scored(all_scored, os.path.join(self.data_dir, "scored"))

        self.complete = complete
        self.incomplete = incomplete
        self.not_found = not_found

        return all_scored

    def _score_one(self, raw: RawListing) -> ScoredListing:
        """Score a single listing against the spec."""
        breakdown = ScoreBreakdown()
        w = self.spec.weights

        # Effective weights: if peza scoring is disabled, redistribute peza weight
        effective_peza_weight = w.peza_zone if self.spec.peza_zone_within_km is not None else 0.0
        # (weights already validated to sum to 100 — we don't re-normalize here;
        #  if peza is skipped we just leave that weight as 0 contribution)

        # ── sqft ──────────────────────────────────────────────────────────────
        sqft_pct = self._sqft_match(raw.listing.sqft)
        breakdown.sqft = round(w.sqft * sqft_pct, 2)

        # ── dock_doors ────────────────────────────────────────────────────────
        dock_pct = self._binary_min(raw.listing.dock_doors, self.spec.dock_doors_min)
        breakdown.dock_doors = round(w.dock_doors * dock_pct, 2)

        # ── clear_height_m ────────────────────────────────────────────────────
        height_pct = self._binary_min(
            raw.listing.clear_height_m, self.spec.clear_height_m_min
        )
        breakdown.clear_height_m = round(w.clear_height_m * height_pct, 2)

        # ── region ────────────────────────────────────────────────────────────
        region_pct = 1.0 if (raw.listing.region in self.spec.regions) else 0.0
        breakdown.region = round(w.region * region_pct, 2)

        # ── corridor_access ───────────────────────────────────────────────────
        if self.spec.corridor_access and raw.enriched.corridor_distances_km:
            corr_pct = corridor_score_pct(
                raw.enriched.corridor_distances_km,
                self.spec.corridor_access,
                CORRIDOR_THRESHOLD_KM,
            )
        elif not self.spec.corridor_access:
            corr_pct = 1.0  # no corridors required → full score
        else:
            corr_pct = 0.0  # corridors required but no distance data
        breakdown.corridor_access = round(w.corridor_access * corr_pct, 2)

        # ── peza_zone ─────────────────────────────────────────────────────────
        if effective_peza_weight > 0 and self.peza_provider and (
            raw.listing.lat is not None and raw.listing.lng is not None
        ):
            nearest_km = self.peza_provider.nearest_zone_km(
                raw.listing.lat, raw.listing.lng
            )
            raw.enriched.peza_zone_km = nearest_km
            peza_pct = (
                1.0
                if nearest_km is not None and nearest_km <= self.spec.peza_zone_within_km
                else 0.0
            )
        else:
            peza_pct = 0.0
        breakdown.peza_zone = round(effective_peza_weight * peza_pct, 2)

        # ── max_flood_risk ────────────────────────────────────────────────────
        flood_risk = raw.enriched.flood_risk
        if flood_risk is None and raw.listing.address:
            # Try to look up from address (extract municipality)
            municipality = self._extract_municipality(raw.listing.address)
            if municipality:
                flood_risk = self.flood_provider.get_risk(municipality)
                raw.enriched.flood_risk = flood_risk

        flood_pct = self._flood_risk_match(flood_risk, self.spec.max_flood_risk)
        breakdown.max_flood_risk = round(w.max_flood_risk * flood_pct, 2)

        # ── Total ─────────────────────────────────────────────────────────────
        total = (
            breakdown.sqft
            + breakdown.dock_doors
            + breakdown.clear_height_m
            + breakdown.region
            + breakdown.corridor_access
            + breakdown.peza_zone
            + breakdown.max_flood_risk
        )
        total = round(min(total, 100.0), 1)

        return ScoredListing(
            id=raw.id,
            source=raw.source,
            url=raw.url,
            scraped_at=raw.scraped_at,
            expires_at=raw.expires_at,
            status=raw.status,
            listing=raw.listing,
            enriched=raw.enriched,
            missing_required=raw.missing_required,
            score=total,
            score_breakdown=breakdown,
        )

    # ── Match logic helpers ───────────────────────────────────────────────────

    def _sqft_match(self, sqft: Optional[float]) -> float:
        """
        100% if sqft ∈ [min, max].
        Linear decay to 0 at ±20% outside the bounds.
        0 beyond that or if None.
        """
        if sqft is None:
            return 0.0
        lo = self.spec.min_sqft
        hi = self.spec.max_sqft

        if lo <= sqft <= hi:
            return 1.0

        if sqft < lo:
            decay_start = lo
            decay_end = lo * (1 - SQFT_DECAY_BAND)
            if sqft <= decay_end:
                return 0.0
            return (sqft - decay_end) / (decay_start - decay_end)

        # sqft > hi
        decay_start = hi
        decay_end = hi * (1 + SQFT_DECAY_BAND)
        if sqft >= decay_end:
            return 0.0
        return (decay_end - sqft) / (decay_end - decay_start)

    @staticmethod
    def _binary_min(value: Optional[float], minimum: float) -> float:
        """100% if value ≥ minimum; 0% otherwise (or if None)."""
        if value is None:
            return 0.0
        return 1.0 if value >= minimum else 0.0

    @staticmethod
    def _flood_risk_match(
        risk: Optional[str], max_risk: str
    ) -> float:
        """100% if risk ≤ max_risk; 0% otherwise."""
        order = {"low": 0, "medium": 1, "high": 2}
        if risk is None:
            return 0.0
        risk_val = order.get(risk.lower(), 99)
        max_val = order.get(max_risk.lower(), 99)
        return 1.0 if risk_val <= max_val else 0.0

    @staticmethod
    def _extract_municipality(address: str) -> Optional[str]:
        """
        Best-effort municipality extraction from a PH address string.
        Returns first comma-separated token that looks like a place name.
        """
        if not address:
            return None
        parts = [p.strip() for p in address.split(",")]
        # Typically: "Unit X, Building, Municipality, Province, Philippines"
        # Skip unit/building-level tokens (usually contain digits or 'unit'/'bldg')
        for part in parts:
            p = part.lower()
            if any(skip in p for skip in ["unit", "bldg", "building", "floor", "room", "#"]):
                continue
            if any(c.isdigit() for c in part):
                continue
            if len(part) > 3:
                return part
        return None
