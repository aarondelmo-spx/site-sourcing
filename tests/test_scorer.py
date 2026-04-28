"""
Tests for sourcing/scorer/engine.py

Covers the 28 test gaps from the eng review test plan.
"""
import os
import sys
import tempfile
from typing import Optional

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sourcing.models import (
    EnrichedFields,
    ListingFields,
    RawListing,
    SpecConfig,
    ScoringWeights,
)
from sourcing.providers.base import FloodRiskProvider, PezaProvider
from sourcing.scorer.engine import ScoringEngine


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_spec(**overrides) -> SpecConfig:
    defaults = dict(
        min_sqft=5000,
        max_sqft=20000,
        dock_doors_min=3,
        clear_height_m_min=6.0,
        regions=["NCR", "Cavite", "Laguna", "Bulacan"],
        corridor_access=["SLEX", "C5"],
        peza_zone_within_km=10,
        max_flood_risk="medium",
        weights=ScoringWeights(
            sqft=25, dock_doors=20, clear_height_m=15, region=20,
            corridor_access=10, peza_zone=5, max_flood_risk=5,
        ),
    )
    defaults.update(overrides)
    return SpecConfig(**defaults)


def make_raw(
    id="test-001",
    sqft=8500,
    dock_doors=4,
    clear_height_m=7.0,
    region="Cavite",
    lat=14.28,
    lng=120.87,
    corridor_distances=None,
    peza_zone_km=3.2,
    flood_risk="low",
    status="active",
) -> RawListing:
    if corridor_distances is None:
        corridor_distances = {"SLEX": 2.1, "C5": 4.8, "NLEX": 35.0, "R10": 22.0}
    return RawListing(
        id=id,
        source="test",
        url=f"https://example.com/{id}",
        status=status,
        listing=ListingFields(
            sqft=sqft,
            dock_doors=dock_doors,
            clear_height_m=clear_height_m,
            region=region,
            lat=lat,
            lng=lng,
        ),
        enriched=EnrichedFields(
            corridor_distances_km=corridor_distances,
            peza_zone_km=peza_zone_km,
            flood_risk=flood_risk,
        ),
    )


class StubFloodProvider(FloodRiskProvider):
    def __init__(self, risk: Optional[str] = "low"):
        self._risk = risk

    def get_risk(self, municipality, province=""):
        return self._risk


class StubPezaProvider(PezaProvider):
    def __init__(self, km: Optional[float] = 3.0):
        self._km = km

    def nearest_zone_km(self, lat, lng):
        return self._km


def make_engine(spec=None, flood_risk="low", peza_km=3.0):
    if spec is None:
        spec = make_spec()
    return ScoringEngine(
        spec=spec,
        flood_provider=StubFloodProvider(flood_risk),
        peza_provider=StubPezaProvider(peza_km),
        data_dir="data",
    )


# ── Scorer: basic ─────────────────────────────────────────────────────────────

def test_full_spec_match_score_100():
    """A listing matching every criterion scores 100."""
    engine = make_engine()
    raw = make_raw(
        sqft=10000,         # in [5000, 20000]
        dock_doors=4,       # ≥ 3
        clear_height_m=7.0, # ≥ 6.0
        region="Cavite",    # in regions list
        corridor_distances={"SLEX": 2.0, "C5": 3.0},  # both within 5km
        peza_zone_km=3.0,   # within 10km
        flood_risk="low",   # ≤ medium
    )
    scored = engine._score_one(raw)
    assert scored.score == 100.0


def test_dock_doors_below_min_scores_zero():
    """dock_doors below minimum → dock_doors field score = 0, total < 100."""
    engine = make_engine()
    raw = make_raw(dock_doors=2)  # min is 3
    scored = engine._score_one(raw)
    assert scored.score_breakdown.dock_doors == 0
    assert scored.score < 100


def test_sqft_at_min_boundary_full_score():
    """sqft exactly at min_sqft → full sqft score."""
    engine = make_engine()
    raw = make_raw(sqft=5000)  # exactly min
    scored = engine._score_one(raw)
    assert scored.score_breakdown.sqft == 25.0


def test_sqft_at_min_minus_15pct_partial_score():
    """sqft at min-15% → partial score (decay, ~50% of weight=25)."""
    engine = make_engine()
    raw = make_raw(sqft=5000 * 0.85)  # 4250, 15% below min
    scored = engine._score_one(raw)
    # Decay range: 5000 to 4000 (20% = 1000 below).
    # At 4250: (4250 - 4000) / (5000 - 4000) = 250/1000 = 0.25 pct
    # field score = 25 * 0.25 = 6.25
    assert 5 < scored.score_breakdown.sqft < 10


def test_sqft_at_min_minus_25pct_zero_score():
    """sqft at min-25% → sqft field score = 0."""
    engine = make_engine()
    raw = make_raw(sqft=5000 * 0.75)  # 3750, 25% below min
    scored = engine._score_one(raw)
    assert scored.score_breakdown.sqft == 0


def test_peza_null_in_spec_skips_field():
    """peza_zone_within_km=null → PEZA field score=0, weights still sum correctly."""
    spec = make_spec(peza_zone_within_km=None)
    engine = ScoringEngine(
        spec=spec,
        flood_provider=StubFloodProvider("low"),
        peza_provider=None,
        data_dir="data",
    )
    raw = make_raw()
    scored = engine._score_one(raw)
    assert scored.score_breakdown.peza_zone == 0
    # Total should reflect other fields correctly
    assert scored.score >= 0


def test_missing_dock_doors_flagged_incomplete():
    """Listing with dock_doors=None → in missing_required, status=incomplete."""
    raw = make_raw(dock_doors=None)
    raw.check_completeness()
    assert "dock_doors" in raw.missing_required
    assert raw.status == "incomplete"


def test_missing_sqft_flagged_incomplete():
    raw = make_raw(sqft=None)
    raw.check_completeness()
    assert "sqft" in raw.missing_required


def test_missing_region_flagged_incomplete():
    raw = make_raw(region=None)
    raw.check_completeness()
    assert "region" in raw.missing_required


# ── Spec validation ───────────────────────────────────────────────────────────

def test_min_sqft_gt_max_sqft_raises():
    with pytest.raises(Exception, match="min_sqft"):
        make_spec(min_sqft=20000, max_sqft=5000)


def test_unknown_region_raises():
    with pytest.raises(Exception, match="Unknown region"):
        make_spec(regions=["Atlantis"])


def test_weights_sum_not_100_raises():
    with pytest.raises(Exception, match="100"):
        SpecConfig(
            min_sqft=5000, max_sqft=20000, dock_doors_min=3,
            clear_height_m_min=6.0, regions=["NCR"],
            weights=ScoringWeights(
                sqft=30, dock_doors=20, clear_height_m=15, region=20,
                corridor_access=10, peza_zone=5, max_flood_risk=5,
            ),  # sums to 105
        )


def test_valid_spec_passes_silently():
    spec = make_spec()
    assert spec.min_sqft == 5000


# ── Flood risk ────────────────────────────────────────────────────────────────

def test_flood_risk_above_max_scores_zero():
    engine = make_engine(flood_risk="high")
    raw = make_raw(flood_risk="high")
    scored = engine._score_one(raw)
    assert scored.score_breakdown.max_flood_risk == 0


def test_flood_risk_at_max_full_score():
    engine = make_engine(flood_risk="medium")
    raw = make_raw(flood_risk="medium")
    scored = engine._score_one(raw)
    assert scored.score_breakdown.max_flood_risk == 5.0


def test_flood_risk_none_scores_zero():
    engine = make_engine(flood_risk=None)
    raw = make_raw(flood_risk=None)
    raw.enriched.flood_risk = None
    raw.listing.address = ""  # No address to look up from
    scored = engine._score_one(raw)
    assert scored.score_breakdown.max_flood_risk == 0


# ── Corridor scoring ──────────────────────────────────────────────────────────

def test_corridor_both_within_full_score():
    engine = make_engine()
    raw = make_raw(corridor_distances={"SLEX": 2.0, "C5": 3.0})
    scored = engine._score_one(raw)
    assert scored.score_breakdown.corridor_access == 10.0


def test_corridor_one_of_two_half_score():
    engine = make_engine()
    raw = make_raw(corridor_distances={"SLEX": 2.0, "C5": 8.0})  # C5 > 5km
    scored = engine._score_one(raw)
    assert scored.score_breakdown.corridor_access == 5.0  # 50% of weight=10


def test_no_corridors_required_full_score():
    spec = make_spec(corridor_access=[])
    engine = make_engine(spec=spec)
    raw = make_raw(corridor_distances={})
    scored = engine._score_one(raw)
    assert scored.score_breakdown.corridor_access == 10.0


# ── PEZA scoring ──────────────────────────────────────────────────────────────

def test_peza_within_threshold_full_score():
    engine = make_engine(peza_km=5.0)
    raw = make_raw()
    scored = engine._score_one(raw)
    assert scored.score_breakdown.peza_zone == 5.0


def test_peza_beyond_threshold_zero_score():
    engine = make_engine(peza_km=15.0)
    spec = make_spec(peza_zone_within_km=10)
    engine = ScoringEngine(
        spec=spec,
        flood_provider=StubFloodProvider("low"),
        peza_provider=StubPezaProvider(15.0),
        data_dir="data",
    )
    raw = make_raw()
    scored = engine._score_one(raw)
    assert scored.score_breakdown.peza_zone == 0


# ── CSV providers ─────────────────────────────────────────────────────────────

def test_flood_risk_csv_known_municipality():
    from sourcing.providers.csv_providers import CsvFloodRiskProvider
    csv_path = os.path.join(ROOT, "data", "ph-flood-risk.csv")
    if not os.path.exists(csv_path):
        pytest.skip("ph-flood-risk.csv not found")
    provider = CsvFloodRiskProvider(csv_path)
    risk = provider.get_risk("Carmona", "Cavite")
    assert risk in {"low", "medium", "high"}


def test_flood_risk_csv_unknown_municipality_returns_none():
    from sourcing.providers.csv_providers import CsvFloodRiskProvider
    csv_path = os.path.join(ROOT, "data", "ph-flood-risk.csv")
    if not os.path.exists(csv_path):
        pytest.skip("ph-flood-risk.csv not found")
    provider = CsvFloodRiskProvider(csv_path)
    result = provider.get_risk("NonexistentCity", "Mars")
    assert result is None


def test_peza_csv_nearest_zone():
    from sourcing.providers.csv_providers import CsvPezaProvider
    csv_path = os.path.join(ROOT, "data", "peza_zones.csv")
    if not os.path.exists(csv_path):
        pytest.skip("peza_zones.csv not found")
    provider = CsvPezaProvider(csv_path)
    # Santa Rosa, Laguna — should be near Laguna Technopark
    km = provider.nearest_zone_km(14.15, 121.17)
    assert km is not None
    assert km < 20  # definitely within 20km


def test_peza_csv_staleness_flag():
    from sourcing.providers.csv_providers import CsvPezaProvider
    csv_path = os.path.join(ROOT, "data", "peza_zones.csv")
    if not os.path.exists(csv_path):
        pytest.skip("peza_zones.csv not found")
    provider = CsvPezaProvider(csv_path)
    # The test CSV has last_updated = 2024-01-01 which is >90 days old
    assert provider.is_stale() is True


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_dedup_same_location_same_sqft_flagged():
    from sourcing.models import ScoredListing, ScoreBreakdown
    from sourcing.storage import flag_duplicates

    def make_scored(id, source, lat, lng, sqft, score):
        return ScoredListing(
            id=id, source=source, url=f"https://example.com/{id}",
            scraped_at="2026-04-01T00:00:00Z",
            status="active",
            listing=ListingFields(sqft=sqft, region="Cavite", lat=lat, lng=lng),
            enriched=EnrichedFields(),
            score=score,
            score_breakdown=ScoreBreakdown(),
        )

    a = make_scored("lamudi-001", "lamudi-ph", 14.28, 120.87, 8500, 80)
    b = make_scored("dotprop-001", "dotproperty-ph", 14.28001, 120.87001, 8500, 75)

    result = flag_duplicates([a, b])
    flagged = [l for l in result if l.possible_duplicate_of]
    assert len(flagged) == 1
    # Lower-scored one (b) should be flagged
    assert flagged[0].id == "dotprop-001"


def test_dedup_different_sqft_not_flagged():
    from sourcing.models import ScoredListing, ScoreBreakdown
    from sourcing.storage import flag_duplicates

    def make_scored(id, source, sqft):
        return ScoredListing(
            id=id, source=source, url=f"https://example.com/{id}",
            scraped_at="2026-04-01T00:00:00Z",
            status="active",
            listing=ListingFields(sqft=sqft, region="Cavite", lat=14.28, lng=120.87),
            enriched=EnrichedFields(),
            score=70,
            score_breakdown=ScoreBreakdown(),
        )

    a = make_scored("lam-001", "lamudi-ph", 8500)
    b = make_scored("dot-001", "dotproperty-ph", 10000)  # >5% different

    result = flag_duplicates([a, b])
    assert all(l.possible_duplicate_of is None for l in result)


# ── Geocoder (unit) ───────────────────────────────────────────────────────────

def test_geocoder_cache_hit_no_api_call(tmp_path, monkeypatch):
    """Cache hit → Google Maps API NOT called."""
    from sourcing.geocoding.geocoder import Geocoder

    cache_path = str(tmp_path / "geocode_cache.json")
    # Pre-populate cache
    import json
    with open(cache_path, "w") as f:
        json.dump(
            {"carmona industrial park cavite philippines": {"lat": 14.3, "lng": 121.0}},
            f,
        )

    call_count = {"n": 0}
    def mock_call_api(self, address):
        call_count["n"] += 1
        return {"lat": 14.3, "lng": 121.0}

    monkeypatch.setattr(Geocoder, "_call_api", mock_call_api)
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")

    geocoder = Geocoder(cache_path=cache_path)
    lat, lng = geocoder.geocode("Carmona Industrial Park, Cavite")
    assert call_count["n"] == 0  # cache hit — no API call
    assert lat == 14.3


def test_geocoder_cache_miss_calls_api(tmp_path, monkeypatch):
    """Cache miss → API called, result persisted."""
    import json
    from sourcing.geocoding.geocoder import Geocoder

    cache_path = str(tmp_path / "geocode_cache.json")

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setattr(
        Geocoder, "_call_api",
        lambda self, addr: {"lat": 14.5, "lng": 121.0},
    )

    geocoder = Geocoder(cache_path=cache_path)
    lat, lng = geocoder.geocode("Some Unknown Address, Laguna")
    assert lat == 14.5
    # Verify persisted
    with open(cache_path) as f:
        cache = json.load(f)
    assert len(cache) == 1


def test_geocoder_missing_api_key_raises():
    from sourcing.geocoding.geocoder import Geocoder, MissingApiKeyError
    import os
    env_backup = os.environ.pop("GOOGLE_MAPS_API_KEY", None)
    try:
        with pytest.raises(MissingApiKeyError):
            Geocoder(api_key=None)
    finally:
        if env_backup:
            os.environ["GOOGLE_MAPS_API_KEY"] = env_backup


def test_geocoder_api_returns_null(tmp_path, monkeypatch):
    """API returns null for address → (None, None), does not crash."""
    from sourcing.geocoding.geocoder import Geocoder

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setattr(Geocoder, "_call_api", lambda self, addr: None)

    geocoder = Geocoder(cache_path=str(tmp_path / "cache.json"))
    lat, lng = geocoder.geocode("Gibberish address that cannot be geocoded")
    assert lat is None
    assert lng is None


def test_geocoder_corrupted_cache(tmp_path, monkeypatch):
    """Corrupted cache.json → falls back to empty cache, does not crash."""
    import json
    from sourcing.geocoding.geocoder import Geocoder

    cache_path = str(tmp_path / "geocode_cache.json")
    with open(cache_path, "w") as f:
        f.write("{invalid json{{")

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setattr(
        Geocoder, "_call_api",
        lambda self, addr: {"lat": 14.0, "lng": 121.0},
    )

    geocoder = Geocoder(cache_path=cache_path)
    assert geocoder.cache_size == 0  # started fresh


# ── Corridor distance ─────────────────────────────────────────────────────────

def test_corridor_distances_returns_all_corridors():
    from sourcing.geocoding.corridor_distance import corridor_distances
    dists = corridor_distances(14.28, 120.87)  # Carmona area
    assert "SLEX" in dists
    assert "NLEX" in dists
    assert "C5" in dists
    assert "R10" in dists
    # Carmona is close to SLEX
    assert dists["SLEX"] < 10


def test_corridor_score_pct_all_within():
    from sourcing.geocoding.corridor_distance import corridor_score_pct
    dists = {"SLEX": 2.0, "C5": 3.0}
    pct = corridor_score_pct(dists, ["SLEX", "C5"], 5.0)
    assert pct == 1.0


def test_corridor_score_pct_none_within():
    from sourcing.geocoding.corridor_distance import corridor_score_pct
    dists = {"SLEX": 10.0, "C5": 12.0}
    pct = corridor_score_pct(dists, ["SLEX", "C5"], 5.0)
    assert pct == 0.0


# ── Re-score flow ─────────────────────────────────────────────────────────────

def test_rescore_no_raw_data_raises(tmp_path):
    """No raw data exists → scorer fails with clear message."""
    spec = make_spec()
    engine = ScoringEngine(
        spec=spec,
        flood_provider=StubFloodProvider("low"),
        peza_provider=StubPezaProvider(3.0),
        data_dir=str(tmp_path),
    )
    with pytest.raises(FileNotFoundError, match="scraper"):
        engine.score_all(raw_dir=str(tmp_path / "raw"))
