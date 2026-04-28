"""
Philippine highway corridor reference points.

Each corridor is represented as a list of (lat, lng) waypoints along the route.
Proximity scoring uses the minimum haversine distance from a listing's coordinates
to any waypoint in the corridor.

Sources:
- SLEX (South Luzon Expressway): Magallanes → Santa Rosa
- NLEX (North Luzon Expressway): Balintawak → Angeles
- C5 (Circumferential Road 5): BGC → Pasig → Quezon City loop
- R10 (Radial Road 10): Navotas → Manila North Harbor corridor

Waypoints are spaced ~5 km apart to provide sufficient coverage without
over-engineering. Add more waypoints if accuracy is critical for edge cases.
"""

from typing import Dict, List, Tuple

# Each entry: (lat, lng)
CorridorWaypoints = List[Tuple[float, float]]

CORRIDORS: Dict[str, CorridorWaypoints] = {
    "SLEX": [
        (14.5503, 121.0189),   # Magallanes interchange
        (14.4798, 121.0271),   # Alabang
        (14.4264, 121.0416),   # Sucat
        (14.3731, 121.0467),   # Muntinlupa / Filinvest
        (14.3042, 121.0671),   # Carmona / Cavite
        (14.2355, 121.0919),   # Biñan, Laguna
        (14.1727, 121.1087),   # Santa Rosa
        (14.1134, 121.1313),   # Calamba (end of SLEX proper)
    ],
    "NLEX": [
        (14.6536, 121.0099),   # Balintawak
        (14.7002, 120.9815),   # Valenzuela / Karuhatan
        (14.7542, 120.9487),   # Meycauayan, Bulacan
        (14.8102, 120.9213),   # Marilao
        (14.8612, 120.9072),   # Bocaue
        (14.9234, 120.8975),   # San Jose del Monte approach
        (14.9858, 120.8788),   # Malolos
        (15.0523, 120.8601),   # Plaridel
        (15.1432, 120.8264),   # San Simon / Angeles approach
    ],
    "C5": [
        (14.5509, 121.0494),   # BGC / Fort Bonifacio
        (14.5769, 121.0621),   # Kalayaan / Guadalupe area
        (14.6058, 121.0801),   # Pasig / Ortigas East
        (14.6345, 121.0924),   # Cainta junction
        (14.6688, 121.0833),   # Pasig boundary / Marikina
        (14.6981, 121.0718),   # Batasan / Commonwealth area
        (14.7212, 121.0493),   # Fairview / Lagro
    ],
    "R10": [
        (14.6674, 120.9617),   # Navotas Fish Port
        (14.6492, 120.9682),   # Tondo / North Harbor
        (14.6311, 120.9746),   # Divisoria / Manila
        (14.6154, 120.9781),   # R10 / España approach
    ],
}


def corridor_names() -> List[str]:
    """Return list of available corridor names."""
    return list(CORRIDORS.keys())
