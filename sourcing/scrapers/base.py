"""
Abstract base class for all site scrapers.

Each source implements ScraperBase and overrides:
  - source_id: str — e.g. "lamudi-ph"
  - scrape_region(region, spec) → List[RawListing]

The orchestrator calls run() which handles:
  - Stale record re-check (re-scrape stale IDs first)
  - Status file updates
  - Geocoding enrichment
  - Completeness flagging
  - Storage persistence
"""
from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sourcing.geocoding.geocoder import Geocoder
from sourcing.geocoding.corridor_distance import corridor_distances
from sourcing.models import RawListing, ScraperStatus, SpecConfig
from sourcing.storage import save_raw, save_status


class ScraperBase(ABC):
    """
    Base class for all source scrapers.

    Subclasses implement `scrape_region` and set `source_id`.
    The base class handles geocoding, enrichment, dedup, and storage.
    """

    source_id: str = "base"           # override in subclass
    REQUEST_DELAY_MIN: float = 1.0    # seconds
    REQUEST_DELAY_MAX: float = 3.0    # seconds

    def __init__(self, geocoder: Geocoder, spec: SpecConfig, data_dir: str = "data"):
        self.geocoder = geocoder
        self.spec = spec
        self.data_dir = data_dir

    @abstractmethod
    def scrape_region(self, region: str) -> List[RawListing]:
        """
        Scrape all listings for a given region from this source.
        Returns a list of RawListing objects (not yet geocoded).
        Must handle pagination internally.
        If a listing URL returns 404: set status = "not_found".
        If required fields are missing: they stay None (flagged later).
        """

    def run(self, status: ScraperStatus) -> List[RawListing]:
        """
        Full scrape run for all spec regions.
        Updates status file as it progresses.
        Returns list of all scraped+enriched RawListings.
        """
        all_listings: List[RawListing] = []

        for region in self.spec.regions:
            status.message = f"[{self.source_id}] Scraping {region}..."
            save_status(status)

            try:
                listings = self.scrape_region(region)
            except Exception as e:
                status.message = f"[{self.source_id}] Error scraping {region}: {e}"
                status.last_error = str(e)
                save_status(status)
                continue

            for listing in listings:
                # Geocode if address present but lat/lng missing
                listing = self._enrich(listing)
                # Flag incomplete
                listing.check_completeness()
                # Set TTL
                if listing.expires_at is None:
                    listing.expires_at = listing.scraped_at + timedelta(days=7)
                # Persist
                save_raw(listing)
                all_listings.append(listing)

                status.fetched += 1
                save_status(status)

            self._random_delay()

        return all_listings

    def _enrich(self, listing: RawListing) -> RawListing:
        """Geocode address → lat/lng, then compute corridor distances."""
        if listing.listing.lat is None and listing.listing.address:
            lat, lng = self.geocoder.geocode(listing.listing.address)
            listing.listing.lat = lat
            listing.listing.lng = lng

        if listing.listing.lat is not None and listing.listing.lng is not None:
            dists = corridor_distances(listing.listing.lat, listing.listing.lng)
            listing.enriched.corridor_distances_km = dists

        return listing

    def _random_delay(self) -> None:
        """Randomized delay between region batches to reduce bot detection."""
        delay = random.uniform(self.REQUEST_DELAY_MIN, self.REQUEST_DELAY_MAX)
        time.sleep(delay)

    @staticmethod
    def make_id(source_id: str, listing_id: str) -> str:
        """Create canonical listing ID."""
        return f"{source_id}-{listing_id}"

    @staticmethod
    def parse_sqft(value: Optional[str]) -> Optional[float]:
        """
        Parse sqft from strings like '8,500 sqm', '8500', '850 sq.m.', etc.
        Returns value in sqft (converts sqm × 10.764 if unit is sqm/m²).
        Returns None if unparseable.
        """
        if value is None:
            return None
        s = str(value).lower().replace(",", "").strip()
        # Detect unit
        is_sqm = any(u in s for u in ["sqm", "sq.m", "sq m", "m²", "m2", "square meter"])
        # Extract number
        import re
        match = re.search(r"[\d.]+", s)
        if not match:
            return None
        try:
            num = float(match.group())
        except ValueError:
            return None
        if is_sqm:
            return round(num * 10.764, 1)
        return round(num, 1)

    @staticmethod
    def parse_float(value: Optional[str]) -> Optional[float]:
        """Parse a numeric string, return None on failure."""
        if value is None:
            return None
        try:
            return float(str(value).replace(",", "").strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def parse_int(value: Optional[str]) -> Optional[int]:
        """Parse an integer string, return None on failure."""
        if value is None:
            return None
        try:
            return int(str(value).replace(",", "").strip().split(".")[0])
        except (ValueError, TypeError):
            return None
