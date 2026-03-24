# ============================================================
#  osrm_client.py  –  OSRM REST helpers
# ============================================================

import requests
import numpy as np
import logging
from typing import Dict, List, Optional, Tuple
import config

log = logging.getLogger(__name__)

SESSION = requests.Session()


def _osrm_available() -> bool:
    try:
        r = SESSION.get(f"{config.OSRM_URL}/route/v1/driving/0,0;0,0", timeout=3)
        return r.status_code < 500
    except Exception:
        return False


def build_matrix_from_osrm(coords: List[Tuple[float, float]]) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build NxN distance (m) and duration (s) matrices via OSRM /table.
    coords: list of (lat, lon)
    """
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{config.OSRM_URL}/table/v1/driving/{coord_str}?annotations=distance,duration"

    try:
        r = SESSION.get(url, timeout=180)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok":
            raise ValueError(f"OSRM error: {data.get('message')}")

        dist = np.array(data["distances"])    # metres
        dur  = np.array(data["durations"])    # seconds
        return dist, dur

    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "Cannot reach OSRM server. "
            "Start it with: docker run -p 5000:5000 osrm/osrm-backend "
            "osrm-routed --algorithm mld /data/mongolia-latest.osrm"
        )


def get_route_geometry(waypoints: List[Tuple[float, float]]) -> Optional[List[List[float]]]:
    """
    Get polyline coordinates for a sequence of (lat, lon) waypoints.
    Returns list of [lon, lat] points or None on failure.
    """
    if len(waypoints) < 2:
        return None

    coord_str = ";".join(f"{lon},{lat}" for lat, lon in waypoints)
    url = (
        f"{config.OSRM_URL}/route/v1/driving/{coord_str}"
        "?overview=full&geometries=geojson&steps=false"
    )

    try:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok":
            return None
        coords = data["routes"][0]["geometry"]["coordinates"]
        return coords  # [[lon, lat], ...]
    except Exception as e:
        log.warning(f"Route geometry failed: {e}")
        return None


def get_route_geometries_batch(
    vehicle_waypoints: Dict[str, List[Tuple[float, float]]]
) -> Dict[str, Optional[List[List[float]]]]:
    """
    Fetch geometries for multiple vehicle routes.
    vehicle_waypoints: {vehicle_id: [(lat, lon), ...]}
    Returns: {vehicle_id: [[lon, lat], ...] or None}
    """
    result = {}
    for vid, wps in vehicle_waypoints.items():
        result[vid] = get_route_geometry(wps)
    return result