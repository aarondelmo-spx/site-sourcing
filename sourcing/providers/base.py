"""Abstract provider interfaces for flood risk and PEZA zone data."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class FloodRiskProvider(ABC):
    """Returns flood risk classification for a municipality."""

    @abstractmethod
    def get_risk(self, municipality: str, province: str = "") -> Optional[str]:
        """
        Return 'low', 'medium', or 'high'.
        Return None if municipality is not found in the data source.
        """

    def risk_level_int(self, risk: Optional[str]) -> int:
        """Convert risk string to int for comparison. None → -1 (unknown)."""
        mapping = {"low": 0, "medium": 1, "high": 2}
        if risk is None:
            return -1
        return mapping.get(risk.lower(), -1)


class PezaProvider(ABC):
    """Returns distance (km) from a lat/lng point to the nearest PEZA zone."""

    @abstractmethod
    def nearest_zone_km(self, lat: float, lng: float) -> Optional[float]:
        """
        Return straight-line distance (km) to nearest PEZA zone.
        Return None if PEZA data is unavailable.
        """
