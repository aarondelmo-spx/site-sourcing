"""
CSV-backed implementations of FloodRiskProvider and PezaProvider.

These are the primary Phase 1 implementations. Data is read from:
  - data/ph-flood-risk.csv
  - data/peza_zones.csv

Both CSVs can be updated manually without touching code.
"""
from __future__ import annotations

import csv
import math
import os
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from sourcing.providers.base import FloodRiskProvider, PezaProvider

# PEZA CSV staleness threshold — show warning in dashboard if older than this
PEZA_STALE_DAYS = 90


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance between two lat/lng points in kilometres."""
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


class CsvFloodRiskProvider(FloodRiskProvider):
    """
    Loads ph-flood-risk.csv and looks up risk by municipality name.
    Match is case-insensitive and strips common punctuation.
    """

    def __init__(self, csv_path: str):
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Flood risk CSV not found: {csv_path}")
        self._data: Dict[str, str] = {}  # "municipality|province" → risk_level
        self._load(csv_path)

    def _load(self, path: str) -> None:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                muni = self._normalize(row.get("municipality", ""))
                prov = self._normalize(row.get("province", ""))
                risk = row.get("risk_level", "").strip().lower()
                if muni and risk in {"low", "medium", "high"}:
                    # Index by municipality alone and by municipality+province
                    self._data[muni] = risk
                    if prov:
                        self._data[f"{muni}|{prov}"] = risk

    @staticmethod
    def _normalize(s: str) -> str:
        return s.lower().strip().replace("ñ", "n").replace(",", "")

    def get_risk(self, municipality: str, province: str = "") -> Optional[str]:
        muni_norm = self._normalize(municipality)
        prov_norm = self._normalize(province)
        # Try exact match with province first, then municipality alone
        key_full = f"{muni_norm}|{prov_norm}"
        if key_full in self._data:
            return self._data[key_full]
        return self._data.get(muni_norm)


class CsvPezaProvider(PezaProvider):
    """
    Loads peza_zones.csv and returns distance (km) to nearest zone.
    Also exposes staleness check for the dashboard warning banner.
    """

    def __init__(self, csv_path: str):
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"PEZA zones CSV not found: {csv_path}")
        self._zones: List[Tuple[float, float]] = []  # (lat, lng) per zone
        self._last_updated: Optional[date] = None
        self._load(csv_path)

    def _load(self, path: str) -> None:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    lat = float(row["lat"])
                    lng = float(row["lng"])
                    self._zones.append((lat, lng))
                    # Track the most recent last_updated date across all rows
                    raw_date = row.get("last_updated", "").strip()
                    if raw_date:
                        d = datetime.strptime(raw_date, "%Y-%m-%d").date()
                        if self._last_updated is None or d > self._last_updated:
                            self._last_updated = d
                except (ValueError, KeyError):
                    continue  # skip malformed rows

    def nearest_zone_km(self, lat: float, lng: float) -> Optional[float]:
        if not self._zones:
            return None
        return min(
            _haversine_km(lat, lng, zlat, zlng)
            for zlat, zlng in self._zones
        )

    def is_stale(self, threshold_days: int = PEZA_STALE_DAYS) -> bool:
        """Return True if the CSV data is older than threshold_days."""
        if self._last_updated is None:
            return True
        return (date.today() - self._last_updated).days > threshold_days

    def days_since_update(self) -> Optional[int]:
        if self._last_updated is None:
            return None
        return (date.today() - self._last_updated).days
