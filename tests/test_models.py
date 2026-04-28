"""Tests for spec validation and model loading."""
import os
import sys
import tempfile

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sourcing.models import SpecConfig, ScoringWeights, load_spec


def write_spec(tmp_path, **overrides):
    defaults = {
        "min_sqft": 5000, "max_sqft": 20000,
        "dock_doors_min": 3, "clear_height_m_min": 6.0,
        "regions": ["NCR", "Cavite"],
        "corridor_access": ["SLEX"],
        "peza_zone_within_km": 10,
        "max_flood_risk": "medium",
        "weights": {
            "sqft": 25, "dock_doors": 20, "clear_height_m": 15,
            "region": 20, "corridor_access": 10,
            "peza_zone": 5, "max_flood_risk": 5,
        },
    }
    defaults.update(overrides)
    path = str(tmp_path / "spec.yaml")
    with open(path, "w") as f:
        yaml.dump(defaults, f)
    return path


def test_load_spec_valid(tmp_path):
    path = write_spec(tmp_path)
    spec = load_spec(path)
    assert spec.min_sqft == 5000


def test_load_spec_min_gt_max_raises(tmp_path):
    path = write_spec(tmp_path, min_sqft=20000, max_sqft=5000)
    with pytest.raises(Exception, match="min_sqft"):
        load_spec(path)


def test_load_spec_unknown_region_raises(tmp_path):
    path = write_spec(tmp_path, regions=["Gondor"])
    with pytest.raises(Exception, match="Unknown region"):
        load_spec(path)


def test_load_spec_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_spec("/nonexistent/spec.yaml")


def test_scoring_weights_sum_validates():
    # Valid
    w = ScoringWeights(
        sqft=25, dock_doors=20, clear_height_m=15, region=20,
        corridor_access=10, peza_zone=5, max_flood_risk=5,
    )
    assert w.sqft == 25

    # Invalid
    with pytest.raises(Exception, match="100"):
        ScoringWeights(
            sqft=30, dock_doors=20, clear_height_m=15, region=20,
            corridor_access=10, peza_zone=5, max_flood_risk=5,
        )


def test_scraper_base_parse_sqft():
    from sourcing.scrapers.base import ScraperBase
    assert ScraperBase.parse_sqft("8,500 sqm") == pytest.approx(91504.4, rel=0.01)
    assert ScraperBase.parse_sqft("8500") == 8500.0
    assert ScraperBase.parse_sqft("850 sq.m.") == pytest.approx(9149.4, rel=0.01)
    assert ScraperBase.parse_sqft(None) is None
    assert ScraperBase.parse_sqft("no number here") is None


def test_scraper_base_parse_sqft_sqft_unit():
    """Values with no unit are treated as sqft directly."""
    from sourcing.scrapers.base import ScraperBase
    result = ScraperBase.parse_sqft("8500 sqft")
    assert result == 8500.0
