"""
Compute haversine distance from a lat/lng point to each highway corridor.

Uses the hardcoded waypoints in sourcing/config/corridors.py.
No geocoding API calls — pure math.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

from sourcing.config.corridors import CORRIDORS


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
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


def corridor_distances(lat: float, lng: float) -> Dict[str, float]:
    """
    Return minimum distance (km) from (lat, lng) to each known corridor.

    Result: {"SLEX": 2.1, "NLEX": 34.5, "C5": 8.2, "R10": 15.0}
    """
    result: Dict[str, float] = {}
    for name, waypoints in CORRIDORS.items():
        distances = [
            _haversine_km(lat, lng, wlat, wlng)
            for wlat, wlng in waypoints
        ]
        result[name] = round(min(distances), 2)
    return result


def within_km(distances: Dict[str, float], corridors: list, threshold_km: float = 5.0) -> Dict[str, bool]:
    """
    For each required corridor, return True if within threshold.
    Corridors not in distances dict are treated as unknown (False).
    """
    return {
        c: (distances.get(c, float("inf")) <= threshold_km)
        for c in corridors
    }


def corridor_score_pct(
    distances: Dict[str, Optional[float]],
    required_corridors: list,
    threshold_km: float = 5.0,
) -> float:
    """
    Returns 0.0–1.0: fraction of required corridors within threshold_km.
    0.0 if required_corridors is empty (no corridors required — full score).
    """
    if not required_corridors:
        return 1.0
    hits = sum(
        1 for c in required_corridors
        if distances.get(c) is not None and distances[c] <= threshold_km
    )
    return hits / len(required_corridors)
