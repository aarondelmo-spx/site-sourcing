"""
Shared data models for the SPX site sourcing pipeline.

These models are the contract between scraper, geocoder, scorer, and dashboard.
All modules import from here — never from each other.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Spec ──────────────────────────────────────────────────────────────────────

VALID_REGIONS = {"NCR", "Cavite", "Laguna", "Bulacan", "Rizal", "Pampanga", "Batangas"}
VALID_CORRIDORS = {"SLEX", "NLEX", "C5", "R10"}
VALID_FLOOD_RISK = {"low", "medium", "high"}


class ScoringWeights(BaseModel):
    sqft: float = 25
    dock_doors: float = 20
    clear_height_m: float = 15
    region: float = 20
    corridor_access: float = 10
    peza_zone: float = 5
    max_flood_risk: float = 5

    @model_validator(mode="after")
    def weights_sum_to_100(self) -> "ScoringWeights":
        total = (
            self.sqft + self.dock_doors + self.clear_height_m +
            self.region + self.corridor_access + self.peza_zone +
            self.max_flood_risk
        )
        if abs(total - 100.0) > 0.01:
            raise ValueError(
                f"Scoring weights must sum to 100, got {total:.2f}. "
                "Check spec.yaml weights section."
            )
        return self


class SpecConfig(BaseModel):
    min_sqft: float = Field(..., gt=0)
    max_sqft: float = Field(..., gt=0)
    dock_doors_min: int = Field(..., ge=0)
    clear_height_m_min: float = Field(..., gt=0)
    regions: List[str]
    corridor_access: List[str] = []
    peza_zone_within_km: Optional[float] = None
    max_flood_risk: Literal["low", "medium", "high"] = "medium"
    power_supply: str = "reliable"
    weights: ScoringWeights = Field(default_factory=ScoringWeights)

    @field_validator("min_sqft")
    @classmethod
    def min_lt_max(cls, v: float) -> float:
        return v  # cross-field check in model_validator below

    @model_validator(mode="after")
    def min_sqft_lt_max(self) -> "SpecConfig":
        if self.min_sqft >= self.max_sqft:
            raise ValueError(
                f"min_sqft ({self.min_sqft}) must be less than max_sqft ({self.max_sqft})"
            )
        return self

    @field_validator("regions", mode="before")
    @classmethod
    def validate_regions(cls, v: list) -> list:
        invalid = [r for r in v if r not in VALID_REGIONS]
        if invalid:
            raise ValueError(
                f"Unknown region(s): {invalid}. "
                f"Valid: {sorted(VALID_REGIONS)}"
            )
        return v

    @field_validator("corridor_access", mode="before")
    @classmethod
    def validate_corridors(cls, v: list) -> list:
        if not v:
            return v
        invalid = [c for c in v if c not in VALID_CORRIDORS]
        if invalid:
            raise ValueError(
                f"Unknown corridor(s): {invalid}. "
                f"Valid: {sorted(VALID_CORRIDORS)}"
            )
        return v


# ── Listing (raw) ─────────────────────────────────────────────────────────────

class ListingFields(BaseModel):
    title: str = ""
    sqft: Optional[float] = None
    dock_doors: Optional[int] = None
    clear_height_m: Optional[float] = None
    address: str = ""
    region: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    price_php: Optional[float] = None
    price_unit: Optional[str] = None   # e.g. "per sqm/month"
    raw_extras: Dict = Field(default_factory=dict)  # source-specific fields


class EnrichedFields(BaseModel):
    corridor_distances_km: Dict[str, Optional[float]] = Field(default_factory=dict)
    peza_zone_km: Optional[float] = None
    flood_risk: Optional[Literal["low", "medium", "high"]] = None


class RawListing(BaseModel):
    id: str                          # "{source}-{listing-id}" — dedup key
    source: str                      # "lamudi-ph" | "dotproperty-ph"
    url: str
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    status: Literal["active", "stale", "not_found", "incomplete"] = "active"
    listing: ListingFields = Field(default_factory=ListingFields)
    enriched: EnrichedFields = Field(default_factory=EnrichedFields)
    missing_required: List[str] = Field(default_factory=list)

    def is_complete(self) -> bool:
        """Return True if all required fields for scoring are present."""
        return len(self.missing_required) == 0

    def check_completeness(self) -> "RawListing":
        """Populate missing_required and set status = incomplete if needed."""
        missing = []
        if self.listing.sqft is None:
            missing.append("sqft")
        if self.listing.dock_doors is None:
            missing.append("dock_doors")
        if self.listing.region is None:
            missing.append("region")
        self.missing_required = missing
        if missing and self.status == "active":
            self.status = "incomplete"
        return self


# ── Scored listing ────────────────────────────────────────────────────────────

class ScoreBreakdown(BaseModel):
    sqft: float = 0
    dock_doors: float = 0
    clear_height_m: float = 0
    region: float = 0
    corridor_access: float = 0
    peza_zone: float = 0
    max_flood_risk: float = 0


class ScoredListing(BaseModel):
    """A RawListing after scoring — the final output written to data/scored/."""
    id: str
    source: str
    url: str
    scraped_at: datetime
    expires_at: Optional[datetime] = None
    status: str
    listing: ListingFields
    enriched: EnrichedFields
    missing_required: List[str] = Field(default_factory=list)
    score: float = 0
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    possible_duplicate_of: Optional[str] = None  # id of suspected duplicate


# ── Status (scraper progress) ─────────────────────────────────────────────────

class ScraperStatus(BaseModel):
    state: Literal["idle", "running", "done", "error"] = "idle"
    pid: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    total: int = 0
    fetched: int = 0
    message: str = ""
    last_error: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_spec(path: str = "spec.yaml") -> SpecConfig:
    """Load and validate spec.yaml. Raises ValidationError with clear message on failure."""
    import yaml  # lazy import — not needed in every module

    if not os.path.exists(path):
        raise FileNotFoundError(f"spec.yaml not found at: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return SpecConfig(**raw)
