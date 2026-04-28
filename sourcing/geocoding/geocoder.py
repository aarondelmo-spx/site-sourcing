"""
Nominatim (OpenStreetMap) geocoding with persistent file-based cache.

No API key required. Free public instance at nominatim.openstreetmap.org.
Rate limit: 1 request/second — enforced by REQUEST_DELAY_S = 1.1s.
The persistent cache means each unique address is only ever hit once.

Cache is stored at data/geocode_cache.json.
Format: { "normalized_address": {"lat": float, "lng": float} }

On cache hit: no HTTP call made.
On cache miss: Nominatim called, result persisted immediately.

Switch to Google Maps later:
  Set GOOGLE_MAPS_API_KEY env var and change BACKEND = "google".
  The rest of the codebase does not change.
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

BACKEND = os.environ.get("GEOCODING_BACKEND", "nominatim")  # "nominatim" | "google"


class MissingApiKeyError(RuntimeError):
    """Raised at startup if Google Maps backend is selected but key is missing."""


class GeocodingError(RuntimeError):
    """Raised when the geocoding service returns an unexpected error."""


class Geocoder:
    """
    Geocoder with persistent cache.

    Defaults to Nominatim (free, no key needed).
    Set GEOCODING_BACKEND=google and GOOGLE_MAPS_API_KEY to switch.

    Usage:
        geocoder = Geocoder()
        lat, lng = geocoder.geocode("Carmona Industrial Park, Carmona, Cavite")
        # Returns (None, None) if unresolvable — never raises for null results.
    """

    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
    GMAPS_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    # Nominatim requires ≥1s between requests per usage policy
    NOMINATIM_DELAY_S = 1.1
    GMAPS_DELAY_S = 0.15

    def __init__(
        self,
        cache_path: str = CACHE_PATH_DEFAULT,
        backend: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self._backend = backend or BACKEND
        self._cache_path = os.path.abspath(cache_path)
        self._cache: dict = self._load_cache()

        if self._backend == "google":
            self._api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "")
            if not self._api_key:
                raise MissingApiKeyError(
                    "GEOCODING_BACKEND=google but GOOGLE_MAPS_API_KEY is not set. "
                    "Either set the key or leave GEOCODING_BACKEND unset to use "
                    "Nominatim (free, no key required)."
                )
        else:
            self._api_key = None

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
        s = address.lower().strip()
        s = re.sub(r"[,\.\-]+", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s

    # ── Public API ────────────────────────────────────────────────────────────

    def geocode(self, address: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Resolve an address to (lat, lng).
        Returns (None, None) if unresolvable — never raises for null results.
        Appends ', Philippines' if not present (improves PH accuracy on both backends).
        """
        if not address or not address.strip():
            return None, None

        query = address.strip()
        if "philippines" not in query.lower():
            query = query + ", Philippines"

        key = self._normalize_address(query)

        if key in self._cache:
            entry = self._cache[key]
            if entry is None:
                return None, None
            return entry.get("lat"), entry.get("lng")

        # Cache miss — call geocoding service
        if self._backend == "google":
            result = self._call_google(query)
            delay = self.GMAPS_DELAY_S
        else:
            result = self._call_nominatim(query)
            delay = self.NOMINATIM_DELAY_S

        self._cache[key] = result
        self._save_cache()
        time.sleep(delay)

        if result is None:
            return None, None
        return result["lat"], result["lng"]

    # ── Nominatim ─────────────────────────────────────────────────────────────

    def _call_nominatim(self, address: str) -> Optional[dict]:
        """
        Call Nominatim OSM geocoding API.
        Returns {"lat": float, "lng": float} or None.
        """
        params = {
            "q": address,
            "format": "json",
            "limit": 1,
            "countrycodes": "ph",
        }
        headers = {
            # Nominatim usage policy requires a valid User-Agent
            "User-Agent": "SPX-SiteSourcing/1.0 (internal logistics tool; panpreeyakorn@gmail.com)",
        }
        try:
            resp = requests.get(
                self.NOMINATIM_URL, params=params, headers=headers, timeout=15
            )
            resp.raise_for_status()
            results = resp.json()
        except requests.RequestException as e:
            raise GeocodingError(f"Nominatim request failed: {e}") from e

        if not results:
            return None

        first = results[0]
        try:
            return {"lat": float(first["lat"]), "lng": float(first["lon"])}
        except (KeyError, ValueError):
            return None

    # ── Google Maps (future) ──────────────────────────────────────────────────

    def _call_google(self, address: str) -> Optional[dict]:
        """Call Google Maps Geocoding API. Returns {"lat": ..., "lng": ...} or None."""
        params = {"address": address, "key": self._api_key, "region": "ph"}
        try:
            resp = requests.get(self.GMAPS_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise GeocodingError(f"Google Maps request failed: {e}") from e

        status = data.get("status")
        if status == "ZERO_RESULTS":
            return None
        if status != "OK":
            raise GeocodingError(
                f"Google Maps returned status '{status}' for: {address}"
            )

        results = data.get("results", [])
        if not results:
            return None

        loc = results[0]["geometry"]["location"]
        return {"lat": loc["lat"], "lng": loc["lng"]}

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    @property
    def backend(self) -> str:
        return self._backend
