# ============================================================
#  data_loader.py  –  Load and validate input data
#  ✅ No structural changes needed for MongoDB.
#     The only real change: validation now accepts a plain set
#     of matrix IDs instead of a DataFrame, because we no longer
#     store DataFrames in SQLAlchemy — we pass the raw sets around.
# ============================================================

import pandas as pd
import numpy as np
import io
from datetime import time
from typing import Dict, List, Optional, Tuple
import config

# ── Helpers ──────────────────────────────────────────────────

def _norm_id(x) -> str:
    """Strip leading zeros so matrix keys match store IDs."""
    try:
        return str(int(str(x).strip()))
    except Exception:
        return str(x).strip()


def _parse_time_to_seconds(t) -> int:
    """Convert various time formats → seconds since midnight."""
    if t is None or (isinstance(t, float) and np.isnan(t)):
        return 0
    if isinstance(t, time):
        return t.hour * 3600 + t.minute * 60 + t.second
    if isinstance(t, str):
        parts = t.strip().split(":")
        try:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
            return h * 3600 + m * 60 + s
        except Exception:
            return 0
    # pandas Timedelta
    try:
        total = int(t.total_seconds())
        return total
    except Exception:
        return 0


# ── Store Loader ─────────────────────────────────────────────
# ✅ UNCHANGED — returns plain list of dicts, perfect for MongoDB

def load_stores(file_bytes: bytes, sheet: str = config.STORE_SHEET) -> List[Dict]:
    """Parse store Excel sheet → list of store dicts."""
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet, dtype=str)

    for col in [config.COL_LAT, config.COL_LON,
                config.COL_DRY_CBM, config.COL_DRY_KG,
                config.COL_COLD_CBM, config.COL_COLD_KG]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df = df.dropna(subset=[config.COL_LAT, config.COL_LON])

    stores = []
    for _, row in df.iterrows():
        raw_id  = str(row[config.COL_STORE_ID]).strip()
        norm    = _norm_id(raw_id)

        open_s  = _parse_time_to_seconds(row.get(config.COL_OPEN))
        close_s = _parse_time_to_seconds(row.get(config.COL_CLOSE))

        if close_s == 0 or close_s <= open_s:
            close_s = 86399

        dry_cbm  = float(row.get(config.COL_DRY_CBM,  0) or 0)
        dry_kg   = float(row.get(config.COL_DRY_KG,   0) or 0)
        cold_cbm = float(row.get(config.COL_COLD_CBM, 0) or 0)
        cold_kg  = float(row.get(config.COL_COLD_KG,  0) or 0)

        stores.append({
            "store_id"    : raw_id,
            "node_id"     : norm,
            "eng_name"    : str(row.get(config.COL_ENG_NAME, "")),
            "mn_name"     : str(row.get(config.COL_MN_NAME,  "")),
            "address"     : str(row.get(config.COL_ADDR,     "")),
            "detail_addr" : str(row.get(config.COL_DTL_ADDR, "")),
            "lat"         : float(row[config.COL_LAT]),
            "lon"         : float(row[config.COL_LON]),
            "open_s"      : open_s,
            "close_s"     : close_s,
            "dry_cbm"     : dry_cbm,
            "dry_kg"      : dry_kg,
            "cold_cbm"    : cold_cbm,
            "cold_kg"     : cold_kg,
            "has_dry"     : dry_kg > 0 or dry_cbm > 0,
            "has_cold"    : cold_kg > 0 or cold_cbm > 0,
        })

    return stores


# ── Vehicle Loader ────────────────────────────────────────────
# ✅ UNCHANGED — returns plain list of dicts, perfect for MongoDB

def load_vehicles(file_bytes: bytes, sheet: str = config.VEHICLE_SHEET) -> List[Dict]:
    """Parse vehicle Excel sheet → list of vehicle dicts."""
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet)

    for col in [config.COL_CAP_KG, config.COL_CAP_M3,
                config.COL_FUEL_COST, config.COL_VEHICLE_COST, config.COL_LABOR_COST]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    vehicles = []
    for _, row in df.iterrows():
        depot = str(row[config.COL_DEPOT]).strip()
        fleet = config.DEPOT_VEHICLE_MAP.get(depot, "DRY")

        vehicles.append({
            "truck_id"     : str(row[config.COL_TRUCK_ID]).strip(),
            "description"  : str(row.get(config.COL_DESCRIPTION, "")),
            "depot"        : depot,
            "fleet"        : fleet,
            "cap_kg"       : float(row[config.COL_CAP_KG]),
            "cap_m3"       : float(row[config.COL_CAP_M3]),
            "fuel_cost_km" : float(row[config.COL_FUEL_COST]),
            "vehicle_cost" : float(row[config.COL_VEHICLE_COST]),
            "labor_cost"   : float(row[config.COL_LABOR_COST]),
        })

    return vehicles


# ── Matrix Loader ─────────────────────────────────────────────
# ✅ UNCHANGED — still returns DataFrames used by the solver in memory.
#    The bytes themselves are saved to GridFS via save_matrix_bytes().

def load_matrix(file_bytes: bytes) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load distance (m) and duration (min) matrices.
    Returns (distance_df, duration_df) with string index/columns.
    """
    dur_df  = pd.read_excel(io.BytesIO(file_bytes), sheet_name=config.DURATION_SHEET, index_col=0)
    dist_df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=config.DISTANCE_SHEET, index_col=0)

    dur_df.index   = [_norm_id(x) for x in dur_df.index]
    dur_df.columns = [_norm_id(x) for x in dur_df.columns]
    dist_df.index   = [_norm_id(x) for x in dist_df.index]
    dist_df.columns = [_norm_id(x) for x in dist_df.columns]

    return dist_df, dur_df


# ── Validation ────────────────────────────────────────────────
# ⚠️  SMALL CHANGE: accepts a plain set of matrix IDs instead of a
#     full DataFrame — keeps this function framework-agnostic.

def validate_data(stores: List[Dict], vehicles: List[Dict],
                  dist_df: pd.DataFrame, dur_df: pd.DataFrame) -> List[str]:
    """Return a list of warning strings (empty = OK).
    
    dist_df / dur_df are still passed as DataFrames (loaded in memory
    by load_matrix). Nothing changes here — validation logic is identical.
    """
    warnings = []
    matrix_ids = set(dist_df.index)

    missing = [s["node_id"] for s in stores if s["node_id"] not in matrix_ids]
    if missing:
        warnings.append(
            f"{len(missing)} stores not found in distance matrix: "
            f"{missing[:5]}{'...' if len(missing) > 5 else ''}"
        )

    for dc in config.DEPOTS:
        norm = _norm_id(dc)
        if norm not in matrix_ids and dc not in matrix_ids:
            warnings.append(f"Depot '{dc}' not found in distance matrix")

    if not vehicles:
        warnings.append("No vehicles loaded")

    return warnings