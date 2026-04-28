"""
Google Maps Geocoding with persistent file-based cache.

Cache is stored at data/geocode_cache.json.
Format: { "normalized_address": {"lat": float, "lng": float} }

On cache hit: no API call made.
On cache miss: API called, result persisted immediately.

API key is read from env var GOOGLE_MAPS_API_KEY.
If key is missing, raises MissingApiKeyError at init — scraper exits before
making any requests (validated at startup, not mid-run).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional, Tuple

import requests

CACHE_PATH_DEFAULT = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "geocode_cache.json"
)


class MissingApiKeyError(RuntimeError):
    """Raised at startup if GOOGLE_MAPS_API_KEY is not set."""


class GeocodingError(RuntimeError):
    """Raised when the API returns an unexpected error (not just a null result)."""


class Geocoder:
    """
    Geocoder with persistent cache.

    Usage:
        geocoder = Geocoder()   # validates API key at init
        lat, lng = geocoder.geocode("Carmona Industrial Park, Carmona, Cavite, Philippines")
        # Returns (None, None) if address cannot be resolved — does NOT raise.
    """

    GMAPS_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    # Rate limit: stay well under Google's 50 req/sec limit; 0.1s pause is sufficient
    REQUEST_DELAY_S = 0.15

    def __init__(
        self,
        cache_path: str = CACHE_PATH_DEFAULT,
        api_key: Optional[str] = None,
    ):
        self._api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "")
        if not self._api_key:
            raise MissingApiKeyError(
                "GOOGLE_MAPS_API_KEY environment variable is not set. "
                "Set it before running the scraper: "
                "export GOOGLE_MAPS_API_KEY=your_key_here"
            )
        self._cache_path = os.path.abspath(cache_path)
        self._cache: dict = self._load_cache()

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _load_cache(self) -> dict:
        if not os.path.exists(self._cache_path):
            return {}
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Expected dict")
            return data
        except (json.JSONDecodeError, ValueError):
            # Corrupted cache — start fresh, log warning
            print(
                f"[geocoder] WARNING: {self._cache_path} is corrupted. "
                "Starting with empty cache."
            )
            return {}

    def _save_cache(self) -> None:
        os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
        with open(self._cache_path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _normalize_address(address: str) -> str:
        """Lowercase, collapse whitespace, strip punctuation for cache key."""
        s = address.lower().strip()
        s = re.sub(r"[,\.\-]+", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s

    # ── Public API ────────────────────────────────────────────────────────────

    def geocode(self, address: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Resolve an address to (lat, lng).
        Returns (None, None) if unresolvable — never raises for null results.
        Appends ', Philippines' if not present to improve PH accuracy.
        """
        if not address or not address.strip():
            return None, None

        query = address.strip()
        if "philippines" not in query.lower():
            query = query + ", Philippines"

        key = self._normalize_address(query)

        # Cache hit
        if key in self._cache:
            entry = self._cache[key]
            if entry is None:
                return None, None
            return entry.get("lat"), entry.get("lng")

        # Cache miss — call API
        result = self._call_api(query)
        self._cache[key] = result
        self._save_cache()
        time.sleep(self.REQUEST_DELAY_S)

        if result is None:
            return None, None
        return result["lat"], result["lng"]

    def _call_api(self, address: str) -> Optional[dict]:
        """Call Google Maps Geocoding API. Returns {"lat": ..., "lng": ...} or None."""
        params = {"address": address, "key": self._api_key, "region": "ph"}
        try:
            resp = requests.get(self.GMAPS_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise GeocodingError(f"Geocoding API request failed: {e}") from e

        status = data.get("status")
        if status == "ZERO_RESULTS":
            return None
        if status != "OK":
            raise GeocodingError(
                f"Geocoding API returned status '{status}' for address: {address}"
            )

        results = data.get("results", [])
        if not results:
            return None

        location = results[0]["geometry"]["location"]
        return {"lat": location["lat"], "lng": location["lng"]}

    @property
    def cache_size(self) -> int:
        return len(self._cache)
