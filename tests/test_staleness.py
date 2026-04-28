"""Tests for staleness detection and status management."""
import os
import sys
import json
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sourcing.models import RawListing, ListingFields, ScraperStatus
from sourcing.storage import (
    load_status,
    mark_stale,
    reset_status,
    save_raw,
    save_status,
)


def make_raw_with_age(id: str, days_old: int, status="active") -> RawListing:
    scraped_at = datetime.now(timezone.utc) - timedelta(days=days_old)
    return RawListing(
        id=id,
        source="test",
        url=f"https://example.com/{id}",
        status=status,
        scraped_at=scraped_at,
        listing=ListingFields(sqft=8000, region="Cavite"),
    )


def test_record_older_than_7_days_marked_stale(tmp_path):
    raw_dir = str(tmp_path / "raw")
    listing = make_raw_with_age("test-001", days_old=8)
    save_raw(listing, raw_dir)

    count = mark_stale(raw_dir)
    assert count == 1

    # Re-load and verify
    from sourcing.storage import load_raw
    updated = load_raw("test-001", raw_dir)
    assert updated.status == "stale"


def test_record_6_days_old_not_marked_stale(tmp_path):
    raw_dir = str(tmp_path / "raw")
    listing = make_raw_with_age("test-002", days_old=6)
    save_raw(listing, raw_dir)

    count = mark_stale(raw_dir)
    assert count == 0


def test_stale_records_returned_in_stale_ids(tmp_path):
    raw_dir = str(tmp_path / "raw")
    stale = make_raw_with_age("stale-001", days_old=10)
    stale.status = "stale"
    save_raw(stale, raw_dir)

    fresh = make_raw_with_age("fresh-001", days_old=2)
    save_raw(fresh, raw_dir)

    from sourcing.storage import get_stale_ids
    ids = get_stale_ids(raw_dir)
    assert "stale-001" in ids
    assert "fresh-001" not in ids


def test_status_reset(tmp_path):
    status_path = str(tmp_path / "status.json")
    # Set to running
    save_status(ScraperStatus(state="running", pid=12345), status_path)
    # Reset
    reset_status(status_path)
    loaded = load_status(status_path)
    assert loaded.state == "idle"


def test_status_load_corrupted_returns_error(tmp_path):
    status_path = str(tmp_path / "status.json")
    with open(status_path, "w") as f:
        f.write("{bad json}")
    status = load_status(status_path)
    assert status.state == "error"


def test_status_load_missing_returns_idle(tmp_path):
    status_path = str(tmp_path / "no_such_file.json")
    status = load_status(status_path)
    assert status.state == "idle"
