# ============================================================
#  solver.py  v6
#
#  What changed from v5:
#   1. Rural/urban distinction REMOVED — all stores treated equally
#   2. config.CLUSTERING toggle — True = sweep clusters, False = one batch
#   3. Multi-trip timing FIXED — Trip N+1 starts after Trip N returns to
#      depot + RELOAD_TIME_SECONDS (no more overlapping schedules)
#   4. Time-dependent travel times — OSRM base matrix is scaled by
#      HOUR_SPEED_FACTOR[fleet.start_hour] so DRY (13:00 rush) routes
#      are realistically slower than COLD (03:00 clear roads) routes
#   5. Dead code removed — sweep_cluster, rural helpers, etc.
#
#  Architecture:
#    solve()
#      └─ _solve_with_clustering()   (per fleet)
#           ├─ [CLUSTERING=True]  _cluster_stores() → groups by angle
#           │    └─ _solve_fleet_multitrip()  per cluster
#           └─ [CLUSTERING=False] _solve_fleet_multitrip()  all stores
#                  └─ _or_tools_solve()   Trip 1 (all vehicles, offset=0)
#                  └─ _or_tools_solve()   Trip 2 (returned vehicles,
#                                          offset = trip1_return + reload)
#                  └─ ... up to MAX_TRIPS_PER_VEHICLE
# ============================================================

import math
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

import config

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  Geometry helpers
# ════════════════════════════════════════════════════════════

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2.0 * R * math.asin(math.sqrt(max(0.0, a)))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return _haversine_m(lat1, lon1, lat2, lon2) / 1000.0


def _angle_from_depot(depot_lat: float, depot_lon: float,
                      lat: float, lon: float) -> float:
    """Bearing of store from depot in radians (–π to π)."""
    return math.atan2(lat - depot_lat, lon - depot_lon)


# ════════════════════════════════════════════════════════════
#  Time-dependent speed
# ════════════════════════════════════════════════════════════

def _speed_factor(hour: int) -> float:
    """
    Return the speed multiplier for a given hour of day (0-23).

    Sourced from config.HOUR_SPEED_FACTOR.  Values > 1.0 mean
    roads are faster than the OSRM free-flow baseline; < 1.0 means
    congestion.

    The factor is applied as:
        adjusted_travel_time = osrm_travel_time / speed_factor

    Real-system context
    -------------------
    This implements "Option A" from the time-dependent routing
    spectrum:

      A (this) — departure-hour factor on the full matrix.
          Captures fleet-level traffic differences. Simple, fast.
          DRY departs 13:00 (rush, factor≈0.85) → routes 18% slower.
          COLD departs 03:00 (clear, factor≈1.25) → routes 25% faster.

      B — precompute 3 matrices (off-peak/AM-peak/PM-peak), pick
          the one that matches the fleet's typical arrival window.
          More accurate, 3× disk / memory usage.

      C — full arc-level time-dependent routing: each arc's cost
          depends on when the vehicle departs that arc. OR-Tools
          supports this but it is 10× more complex to model.
    """
    return config.HOUR_SPEED_FACTOR.get(hour % 24, 1.0)


# ════════════════════════════════════════════════════════════
#  Matrix helpers
# ════════════════════════════════════════════════════════════

def _matrix_key(node_id: str, all_ids: List[str]) -> Optional[str]:
    return node_id if node_id in all_ids else None


def _build_submatrix(
    dist_df,
    dur_df,
    nodes        : List[Dict],
    depot_name   : str,
    depart_hour  : int,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build NxN distance (metres) and duration (seconds) sub-matrices
    for the given node list.

    Duration is adjusted for time-of-day speed:
        adjusted_time = osrm_minutes × 60 / speed_factor(depart_hour)

    Fallback when a node is missing from the matrix: haversine distance
    at 60 km/h, also scaled by the speed factor.
    """
    all_ids = [str(x) for x in dist_df.index]
    n       = len(nodes)
    dist    = np.zeros((n, n), dtype=np.float64)
    dur     = np.zeros((n, n), dtype=np.float64)
    factor  = _speed_factor(depart_hour)

    def _key(nd: Dict) -> Optional[str]:
        nid = depot_name if nd["is_depot"] else nd["node_id"]
        return _matrix_key(nid, all_ids)

    keys = [_key(nd) for nd in nodes]

    for i in range(n):
        for j in range(n):
            ki, kj = keys[i], keys[j]
            if ki and kj and ki in dist_df.index and kj in dist_df.columns:
                dist[i][j] = float(dist_df.at[ki, kj])
                # dur_df values are minutes → convert to seconds
                dur[i][j]  = float(dur_df.at[ki, kj]) * 60.0
            else:
                d_m = _haversine_m(
                    nodes[i]["lat"], nodes[i]["lon"],
                    nodes[j]["lat"], nodes[j]["lon"],
                )
                dist[i][j] = d_m
                # 60 km/h fallback in seconds
                dur[i][j]  = d_m / (60_000.0 / 3600.0)

    # Apply time-of-day speed factor to all travel times
    if factor != 1.0:
        dur = dur / factor
        log.debug(
            f"Speed factor {factor:.3f} applied for "
            f"{depart_hour:02d}:00 → travel times "
            f"{'longer' if factor < 1 else 'shorter'} than OSRM base"
        )

    return dist, dur


def _depot_travel_times(
    dur_df,
    dist_df,
    depot_name  : str,
    stores      : List[Dict],
    fleet       : str,
    depart_hour : int,
) -> Tuple[np.ndarray, List[str]]:
    """
    Return (travel_times_s, node_id_list) from depot to each eligible store.
    Travel times are speed-adjusted for the fleet's departure hour.
    """
    all_ids = [str(x) for x in dur_df.index]
    dk      = _matrix_key(depot_name, all_ids)
    factor  = _speed_factor(depart_hour)
    nids: List[str]  = []
    durs: List[float] = []

    for s in stores:
        if fleet == "DRY"  and not s["has_dry"]:  continue
        if fleet == "COLD" and not s["has_cold"]: continue

        sk = _matrix_key(s["node_id"], all_ids)
        nids.append(s["node_id"])

        if dk and sk and dk in dur_df.index and sk in dur_df.columns:
            base_s = float(dur_df.at[dk, sk]) * 60.0   # minutes → seconds
            durs.append(base_s / factor)
        else:
            dep  = config.DEPOTS[depot_name]
            d_m  = _haversine_m(dep["lat"], dep["lon"], s["lat"], s["lon"])
            durs.append(d_m / (60_000.0 / 3600.0) / factor)

    return np.array(durs, dtype=np.float64), nids


# ════════════════════════════════════════════════════════════
#  Node builder
# ════════════════════════════════════════════════════════════

def _build_nodes(
    depot      : Dict,
    stores     : List[Dict],
    fleet      : str,
    travel_s   : np.ndarray,
    store_nids : List[str],
    sched      : Dict,
) -> List[Dict]:
    """
    Build the node list. Index 0 is always the depot.

    Time windows are shift-relative (seconds after fleet departure).
    Wall-clock store hours are converted:
        tw_open  = max(0, store_open_wall  - shift_start_wall)
        tw_close = min(max_horizon, store_close_wall - shift_start_wall)

    All-day stores (00:00–23:59) get the full planning horizon.
    """
    shift_s  = sched["start_hour"] * 3600
    max_h_s  = (sched["max_horizon_hour"] - sched["start_hour"]) * 3600

    id_to_travel = dict(zip(store_nids, travel_s))

    nodes: List[Dict] = [{
        "node_id"  : depot["name"],
        "lat"      : depot["lat"],
        "lon"      : depot["lon"],
        "tw_open"  : 0,
        "tw_close" : max_h_s,
        "demand_kg": 0.0,
        "demand_m3": 0.0,
        "is_depot" : True,
        "store"    : None,
        "travel_s" : 0.0,
    }]

    for s in stores:
        if fleet == "DRY"  and not s["has_dry"]:  continue
        if fleet == "COLD" and not s["has_cold"]: continue

        t_s        = float(id_to_travel.get(s["node_id"], 0.0))
        wall_open  = int(s["open_s"])
        wall_close = int(s["close_s"])

        # All-day store?
        is_all_day = (wall_open == 0 and wall_close >= 86398)

        if is_all_day:
            tw_open  = 0
            tw_close = max_h_s
        else:
            tw_open  = max(0, wall_open  - shift_s)
            tw_close = min(max_h_s, wall_close - shift_s)

        # Ensure travel is possible within the window
        if tw_close <= 0 or tw_close <= tw_open:
            # Fallback: give full horizon
            tw_open  = 0
            tw_close = max_h_s

        # If travel time exceeds window close, extend window to allow delivery
        if t_s > tw_close:
            tw_close = min(int(t_s) + 3600, max_h_s)

        nodes.append({
            "node_id"  : s["node_id"],
            "lat"      : s["lat"],
            "lon"      : s["lon"],
            "tw_open"  : int(tw_open),
            "tw_close" : int(tw_close),
            "demand_kg": float(s["dry_kg"]  if fleet == "DRY" else s["cold_kg"]),
            "demand_m3": float(s["dry_cbm"] if fleet == "DRY" else s["cold_cbm"]),
            "is_depot" : False,
            "store"    : s,
            "travel_s" : t_s,
        })

    return nodes


# ════════════════════════════════════════════════════════════
#  Geographic clustering  (used when CLUSTERING=True)
# ════════════════════════════════════════════════════════════

def _cluster_stores(
    stores     : List[Dict],
    depot      : Dict,
    fleet      : str,
    n_clusters : int,
) -> List[List[Dict]]:
    """
    Split fleet-eligible stores into n_clusters geographic groups.

    Method: Sweep (sort by angle from depot, divide into equal slices).
    Stores in the same angular direction naturally share routes, so
    this mimics how a dispatcher manually draws "truck territories"
    on a map.

    Returns list of store-lists.  Empty clusters are dropped.
    """
    eligible = [
        s for s in stores
        if (s["has_dry"] if fleet == "DRY" else s["has_cold"])
    ]
    if not eligible:
        return []

    # Annotate with bearing angle
    for s in eligible:
        s["_angle"] = _angle_from_depot(
            depot["lat"], depot["lon"], s["lat"], s["lon"]
        )

    eligible.sort(key=lambda s: s["_angle"])

    # Divide into equal-size slices
    n        = len(eligible)
    clusters = []
    size     = max(1, math.ceil(n / n_clusters))
    for i in range(0, n, size):
        chunk = eligible[i : i + size]
        if chunk:
            clusters.append(chunk)

    # Remove temp annotation
    for s in eligible:
        s.pop("_angle", None)

    log.info(
        f"[{fleet}] Sweep clustering: {n} stores → "
        f"{len(clusters)} clusters (target={n_clusters})"
    )
    return clusters


# ════════════════════════════════════════════════════════════
#  Core OR-Tools solver  (single trip pass)
# ════════════════════════════════════════════════════════════

def _or_tools_solve(
    fleet        : str,
    depot        : Dict,
    stores       : List[Dict],
    vehicles     : List[Dict],
    dist_df,
    dur_df,
    mode         : str,
    solver_time_s: int,
    trip_num     : int = 1,
) -> Dict:
    """
    Solve a single-trip CVRPTW for the given stores and vehicle list.

    Each vehicle dict may carry a "start_offset" (int, seconds from shift
    start) that encodes when this truck becomes available for this trip.
    Trip 1 → offset=0.  Trip 2 → offset = trip-1 return time + reload.

    Returns:
        {routes, unserved, nodes, fleet}
        Each route carries "return_time_s" (shift-relative seconds when
        the truck arrives back at depot) so the caller can schedule trip N+1.
    """
    sched        = config.FLEET_SCHEDULE[fleet]
    shift_s      = sched["start_hour"] * 3600          # wall-clock shift start
    max_h_s      = (sched["max_horizon_hour"] - sched["start_hour"]) * 3600
    depart_hour  = sched["start_hour"]

    # ── 1. Travel times from depot ─────────────────────────────
    travel_s, store_nids = _depot_travel_times(
        dur_df, dist_df, depot["name"], stores, fleet, depart_hour
    )

    if not store_nids:
        return {"routes": [], "unserved": [], "nodes": [], "fleet": fleet}

    # ── 2. Node list ────────────────────────────────────────────
    nodes = _build_nodes(depot, stores, fleet, travel_s, store_nids, sched)
    n_eligible = len(nodes) - 1

    if n_eligible == 0:
        return {"routes": [], "unserved": [], "nodes": nodes, "fleet": fleet}

    n  = len(nodes)
    nv = len(vehicles)

    # ── 3. Distance + duration matrices ────────────────────────
    dist_mat, dur_mat = _build_submatrix(
        dist_df, dur_df, nodes, depot["name"], depart_hour
    )

    # Scale to decimetres for OR-Tools integer arithmetic
    # (avoids int32 overflow on routes spanning hundreds of km)
    dist_dm  = (dist_mat / 10.0).astype(np.int64)
    # dur_svc: travel + service time — used ONLY for the Time dimension
    # so OR-Tools schedules accurate arrival/departure times at each stop.
    dur_svc  = (dur_mat + config.SERVICE_TIME_SECONDS).astype(np.int64)
    # dur_pure: raw travel time with NO service time — used as arc COST
    # for "fastest" mode.  Keeping service time out of arc cost prevents
    # the solver from gaming the objective by dropping stops (every dropped
    # stop saves SERVICE_TIME_SECONDS from the arc-cost sum).
    dur_pure = dur_mat.astype(np.int64)

    # ── 4. Routing model ────────────────────────────────────────
    manager = pywrapcp.RoutingIndexManager(n, nv, [0] * nv, [0] * nv)
    routing = pywrapcp.RoutingModel(manager)

    DEPOT_NODE = 0   # index 0 is always the depot

    # ── 5. Arc-cost callbacks ───────────────────────────────────
    #
    # Three modes — each has a depot-zero variant so the return-to-depot
    # leg is never penalised in the objective.  Scheduling accuracy is
    # preserved via the Time dimension which uses dur_svc (with service time).
    #
    # FASTEST  — minimise pure travel time between stores (no service-time
    #            bias, no distance metric).
    # CHEAPEST — minimise total monetary cost: fuel ₮ per km × distance,
    #            weighted per-vehicle.  Fixed/labor costs are per-day so they
    #            don't affect route shape, only the reported ₮ total.
    # SHORTEST — minimise total driving distance (km).

    # Scheduling callback — includes service time; only fed to Time dimension.
    def _sched_cb(fi, ti):
        return int(dur_svc[manager.IndexToNode(fi)][manager.IndexToNode(ti)])

    sched_cb_idx = routing.RegisterTransitCallback(_sched_cb)

    if mode == "fastest":
        # Pure travel time, depot arcs free
        def _fast_cb(fi, ti):
            ni, nj = manager.IndexToNode(fi), manager.IndexToNode(ti)
            if nj == DEPOT_NODE:
                return 0
            return int(dur_pure[ni][nj])
        routing.SetArcCostEvaluatorOfAllVehicles(
            routing.RegisterTransitCallback(_fast_cb)
        )

    elif mode == "cheapest":
        # Fuel cost = dist_dm (decimetres) × fuel_cost_km / 10 000
        # Depot arcs free — only inter-store cost matters
        for vi, veh in enumerate(vehicles):
            fpm = veh["fuel_cost_km"] / 10_000.0   # ₮ per decimetre
            def _make_fuel(f):
                def cb(fi, ti):
                    ni, nj = manager.IndexToNode(fi), manager.IndexToNode(ti)
                    if nj == DEPOT_NODE:
                        return 0
                    return int(dist_dm[ni][nj] * f)
                return cb
            routing.SetArcCostEvaluatorOfVehicle(
                routing.RegisterTransitCallback(_make_fuel(fpm)), vi
            )

    else:  # "shortest" — minimise driving distance, depot arcs free
        def _dist_cb(fi, ti):
            ni, nj = manager.IndexToNode(fi), manager.IndexToNode(ti)
            if nj == DEPOT_NODE:
                return 0
            return int(dist_dm[ni][nj])
        routing.SetArcCostEvaluatorOfAllVehicles(
            routing.RegisterTransitCallback(_dist_cb)
        )

    # Small fixed cost per trip discourages unnecessary empty trips
    routing.SetFixedCostOfAllVehicles(config.VEHICLE_FIXED_COST)

    # ── 6. Weight capacity ──────────────────────────────────────
    def _kg_cb(idx):
        return int(nodes[manager.IndexToNode(idx)]["demand_kg"])

    kg_cb = routing.RegisterUnaryTransitCallback(_kg_cb)
    routing.AddDimensionWithVehicleCapacity(
        kg_cb, 0,
        [int(v["cap_kg"]) for v in vehicles],
        True, "CapKg"
    )

    # ── 7. Volume capacity ──────────────────────────────────────
    def _m3_cb(idx):
        return int(nodes[manager.IndexToNode(idx)]["demand_m3"] * config.M3_SCALE)

    m3_cb = routing.RegisterUnaryTransitCallback(_m3_cb)
    routing.AddDimensionWithVehicleCapacity(
        m3_cb, 0,
        [int(v["cap_m3"] * config.M3_SCALE) for v in vehicles],
        True, "CapM3"
    )

    # ── 8. Time-window dimension ────────────────────────────────
    # Must use sched_cb_idx (travel + service time) so cumulative time at each
    # node correctly accounts for unloading before the truck can move on.
    routing.AddDimension(
        sched_cb_idx,
        7_200,      # max slack (2h waiting) — allows early arrivals / waiting
        max_h_s,
        False,
        "Time"
    )
    time_dim = routing.GetDimensionOrDie("Time")

    # Node windows
    for i, nd in enumerate(nodes):
        if nd["is_depot"]:
            continue
        ri  = manager.NodeToIndex(i)
        o, c = nd["tw_open"], nd["tw_close"]
        time_dim.CumulVar(ri).SetRange(o, c)

    # Vehicle start / end windows
    # KEY FIX: start_offset enforces that trip N+1 can't start before
    # trip N finishes + RELOAD_TIME.  Each vehicle has its own offset.
    for vi, veh in enumerate(vehicles):
        start_off = int(veh.get("start_offset", 0))
        time_dim.CumulVar(routing.Start(vi)).SetRange(start_off, max_h_s)
        time_dim.CumulVar(routing.End(vi)).SetRange(start_off, max_h_s)

    # GlobalSpan on the Time dimension would pull routes toward depot-proximity
    # (return leg is in real time).  Capacity constraints balance load instead.
    time_dim.SetGlobalSpanCostCoefficient(0)

    # ── 9. Disjunctions (allow dropping with heavy penalty) ─────
    for i in range(1, n):
        routing.AddDisjunction(
            [manager.NodeToIndex(i)], config.PENALTY_UNSERVED
        )

    # ── 10. Search parameters ───────────────────────────────────
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.seconds = solver_time_s
    params.log_search = False

    solution = routing.SolveWithParameters(params)

    if solution is None:
        log.warning(
            f"[{fleet}] Trip {trip_num}: No solution found "
            f"({n_eligible} stores, {nv} vehicles, {solver_time_s}s)"
        )
        return {
            "routes"  : [],
            "unserved": [
                {
                    "store": nd["store"],
                    "reason": (
                        "Solver found no feasible solution. "
                        "Try increasing solver_time or adding vehicles."
                    ),
                    "node": nd,
                }
                for nd in nodes[1:]
            ],
            "nodes"   : nodes,
            "fleet"   : fleet,
        }

    # ── 11. Extract routes ──────────────────────────────────────
    raw_routes: List[Dict] = []
    served_ids: set = set()

    for vi, veh in enumerate(vehicles):
        idx = routing.Start(vi)
        if routing.IsEnd(solution.Value(routing.NextVar(idx))):
            continue   # vehicle unused

        stops:        List[Dict] = []
        total_dist_m: float      = 0.0
        total_dur_s:  float      = 0.0
        load_kg:      float      = 0.0
        load_m3:      float      = 0.0

        # last_node_idx and last_t track where the truck is when it heads home
        last_ni = 0
        last_t  = int(veh.get("start_offset", 0))   # shift-relative departure

        while not routing.IsEnd(idx):
            ni = manager.IndexToNode(idx)
            nd = nodes[ni]

            if not nd["is_depot"]:
                served_ids.add(nd["node_id"])
                t_solver = solution.Value(time_dim.CumulVar(idx))
                arr_wall = t_solver + shift_s   # absolute wall-clock seconds

                stops.append({
                    "node_id"    : nd["node_id"],
                    "store"      : nd["store"],
                    "arrival_s"  : float(arr_wall),
                    "depart_s"   : float(arr_wall + config.SERVICE_TIME_SECONDS),
                    "demand_kg"  : float(nd["demand_kg"]),
                    "demand_m3"  : float(nd["demand_m3"]),
                    "lat"        : float(nd["lat"]),
                    "lon"        : float(nd["lon"]),
                    "is_next_day": bool(arr_wall >= 86400),
                    "is_rural"   : False,   # kept for backward compat with formatter
                })
                load_kg += nd["demand_kg"]
                load_m3 += nd["demand_m3"]
                last_t   = t_solver + config.SERVICE_TIME_SECONDS
                last_ni  = ni

            nxt = solution.Value(routing.NextVar(idx))
            if not routing.IsEnd(nxt):
                ni2 = manager.IndexToNode(nxt)
                total_dist_m += dist_mat[ni][ni2]
                total_dur_s  += dur_mat[ni][ni2]
            idx = nxt

        if not stops:
            continue

        # Return travel: last stop → depot
        return_leg_s  = float(dur_mat[last_ni][0])   # time back to depot
        return_dist_m = float(dist_mat[last_ni][0])
        total_dist_m += return_dist_m
        total_dur_s  += return_leg_s

        # return_time_s: shift-relative seconds when this truck arrives back
        # This drives Trip N+1 start_offset calculation.
        return_time_s = last_t + return_leg_s

        raw_routes.append({
            "truck_id"      : veh["truck_id"],
            "trip_number"   : trip_num,
            "virtual_id"    : f"{veh['truck_id']}_T{trip_num}",
            "vehicle"       : veh,
            "stops"         : stops,
            "total_dist_m"  : float(total_dist_m),
            "total_dur_s"   : float(total_dur_s),
            "load_kg"       : float(load_kg),
            "load_m3"       : float(load_m3),
            "cap_kg"        : float(veh["cap_kg"]),
            "cap_m3"        : float(veh["cap_m3"]),
            "has_rural"     : False,   # backward compat — rural routes removed
            "return_time_s" : float(return_time_s),
            # shift-relative seconds when this truck was available to depart.
            # Trip 1 → 0.  Trip N+1 → previous return_time + reload gap.
            # Used by output_formatter to show the real departure time per trip.
            "start_offset_s": float(veh.get("start_offset", 0)),
        })

    # ── 12. Unserved diagnosis ──────────────────────────────────
    unserved = [
        {
            "store": nd["store"],
            "reason": _diagnose(nd, vehicles, dist_mat, nodes, sched),
            "node":  nd,
        }
        for nd in nodes[1:]
        if nd["node_id"] not in served_ids
    ]

    return {
        "routes"  : raw_routes,
        "unserved": unserved,
        "nodes"   : nodes,
        "fleet"   : fleet,
    }


# ════════════════════════════════════════════════════════════
#  Sequential multi-trip solver
# ════════════════════════════════════════════════════════════

def _solve_fleet_multitrip(
    fleet        : str,
    depot        : Dict,
    stores       : List[Dict],
    vehicles     : List[Dict],
    dist_df,
    dur_df,
    mode         : str,
    solver_time_s: int,
) -> Dict:
    """
    Sequential multi-trip: solve trip 1, then trip 2 with remaining stores.

    WHY SEQUENTIAL?
    ───────────────
    The OR-Tools virtual-vehicle expansion (one copy per trip slot) treats
    T1 and T2 as two independent trucks.  They can run simultaneously,
    which is physically impossible — one driver can't be in two places.

    Sequential solving fixes this:
      Round 1: Every truck starts at shift_start (start_offset = 0).
               OR-Tools assigns stores to trucks.
      Round 2: Each truck's start_offset = trip-1 return time
               + RELOAD_TIME_SECONDS (parking + reloading).
               Remaining unserved stores are re-solved with this
               updated availability.

    This guarantees:
      - Trip 2 for truck X begins only after Trip 1 for truck X returns.
      - The gap is at least RELOAD_TIME_SECONDS (configurable).
      - No two trips from the same truck overlap.

    Returns merged routes with globally unique trip numbers.
    """
    sched   = config.FLEET_SCHEDULE[fleet]
    max_h_s = (sched["max_horizon_hour"] - sched["start_hour"]) * 3600

    all_routes:  List[Dict] = []
    remaining              = list(stores)

    # shift-relative return time for each truck (0 = available from shift start)
    truck_return: Dict[str, float] = {
        v["truck_id"]: 0.0 for v in vehicles
    }

    for trip_num in range(1, config.MAX_TRIPS_PER_VEHICLE + 1):
        if not remaining:
            break

        # Build vehicle list with correct start_offset for this trip round
        available_vehicles: List[Dict] = []
        for v in vehicles:
            if trip_num == 1:
                offset = 0
            else:
                # Previous trip return + reload gap
                offset = int(truck_return[v["truck_id"]] + config.RELOAD_TIME_SECONDS)

            if offset >= max_h_s:
                log.debug(
                    f"[{fleet}] Truck {v['truck_id']} skipped for trip {trip_num}: "
                    f"available at {offset/3600:.2f}h > shift end {max_h_s/3600:.2f}h"
                )
                continue

            available_vehicles.append({**v, "start_offset": offset})

        if not available_vehicles:
            log.info(
                f"[{fleet}] No trucks available for trip {trip_num} — stopping."
            )
            break

        log.info(
            f"[{fleet}] Trip {trip_num}/{config.MAX_TRIPS_PER_VEHICLE}: "
            f"{len(remaining)} stores, "
            f"{len(available_vehicles)}/{len(vehicles)} trucks, "
            f"{solver_time_s}s budget"
        )
        # Log per-truck start offsets for trip N > 1
        if trip_num > 1:
            offsets_str = ", ".join(
                f"{v['truck_id']}@{v['start_offset']/3600:.2f}h"
                for v in available_vehicles
            )
            log.info(f"[{fleet}]   Truck availability: {offsets_str}")

        res = _or_tools_solve(
            fleet, depot, remaining, available_vehicles,
            dist_df, dur_df, mode, solver_time_s, trip_num
        )

        all_routes.extend(res["routes"])

        # Update each truck's return time so the next round uses it
        for route in res["routes"]:
            tid = route["truck_id"]
            truck_return[tid] = route["return_time_s"]
            log.debug(
                f"[{fleet}] Truck {tid} trip {trip_num}: "
                f"returns at shift+{route['return_time_s']/3600:.2f}h "
                f"({route['return_time_s']/3600 + sched['start_hour']:.2f} wall-clock)"
            )

        # Remove served stores from the pool
        served = {
            stop["node_id"]
            for route in res["routes"]
            for stop in route["stops"]
        }
        prev_len  = len(remaining)
        remaining = [s for s in remaining if s["node_id"] not in served]
        log.info(
            f"[{fleet}] Trip {trip_num} result: "
            f"{len(served)} served, {len(remaining)} remain "
            f"(was {prev_len})"
        )

    # Stores still unserved after all trips
    served_all = {
        stop["node_id"]
        for route in all_routes
        for stop in route["stops"]
    }
    fleet_key = "has_dry" if fleet == "DRY" else "has_cold"
    unserved  = [
        {
            "store": s,
            "reason": (
                f"Not served after {config.MAX_TRIPS_PER_VEHICLE} trip(s). "
                f"Increase Max Trips or add more vehicles."
            ),
            "node": None,
        }
        for s in stores
        if s["node_id"] not in served_all and s.get(fleet_key)
    ]

    return {
        "routes"  : all_routes,
        "unserved": unserved,
        "nodes"   : [],
        "fleet"   : fleet,
    }


# ════════════════════════════════════════════════════════════
#  Clustering wrapper  (respects config.CLUSTERING toggle)
# ════════════════════════════════════════════════════════════

def _solve_with_clustering(
    fleet        : str,
    depot        : Dict,
    stores       : List[Dict],
    vehicles     : List[Dict],
    dist_df,
    dur_df,
    mode         : str,
    solver_time_s: int,
) -> Dict:
    """
    Entry point per fleet.

    CLUSTERING = False → one call to _solve_fleet_multitrip with all stores + all vehicles.
    CLUSTERING = True  → split stores into geographic clusters, solve each cluster
                         with a proportional vehicle slice, merge results.

    The CLUSTERING toggle lets you directly compare solution quality:

        CLUSTERING=false python -c "import uvicorn; uvicorn.run('main:app')"
        # → Run optimize → note total cost, unserved count

        CLUSTERING=true  python -c "import uvicorn; uvicorn.run('main:app')"
        # → Run same input → compare cost / coverage
    """
    n_veh = len(vehicles)
    sched = config.FLEET_SCHEDULE[fleet]
    depart_hour = sched["start_hour"]

    log.info(
        f"[{fleet}] Departs {depart_hour:02d}:00 | "
        f"speed factor {_speed_factor(depart_hour):.2f} | "
        f"CLUSTERING={'ON' if config.CLUSTERING else 'OFF'} | "
        f"{len(stores)} stores | {n_veh} vehicles"
    )

    if not config.CLUSTERING:
        # ── Single-batch solve ─────────────────────────────────
        return _solve_fleet_multitrip(
            fleet, depot, stores, vehicles,
            dist_df, dur_df, mode, solver_time_s
        )

    # ── Clustered solve ────────────────────────────────────────
    n_clusters = min(
        config.MAX_CLUSTERS,
        n_veh,
        max(1, len(stores) // 5),   # at least 5 stores per cluster
    )

    clusters = _cluster_stores(stores, depot, fleet, n_clusters)
    if not clusters:
        return {"routes": [], "unserved": [], "nodes": [], "fleet": fleet}

    n_actual      = len(clusters)
    vehs_per_cl   = max(1, n_veh // n_actual)
    # Use user's solver time, but ensure at least 1 second per cluster
    cluster_time  = max(1, solver_time_s // n_actual)

    all_routes:   List[Dict] = []
    all_unserved: List[Dict] = []

    for i, cluster_stores in enumerate(clusters):
        # Assign proportional vehicle slice to this cluster
        start_vi = (i * vehs_per_cl) % n_veh
        end_vi   = start_vi + vehs_per_cl
        if i == n_actual - 1:
            end_vi = n_veh          # last cluster absorbs any remainder
        cluster_vehicles = vehicles[start_vi:end_vi] or vehicles

        log.info(
            f"[{fleet}] Cluster {i+1}/{n_actual}: "
            f"{len(cluster_stores)} stores | "
            f"trucks [{', '.join(v['truck_id'] for v in cluster_vehicles)}] | "
            f"{cluster_time}s"
        )

        res = _solve_fleet_multitrip(
            fleet, depot, cluster_stores, cluster_vehicles,
            dist_df, dur_df, mode, cluster_time
        )
        all_routes.extend(res["routes"])
        all_unserved.extend(res["unserved"])

    # Fix trip numbers so each truck has consecutive 1, 2, 3 … across clusters
    _renumber_trips(all_routes)

    n_trucks  = len({r["truck_id"] for r in all_routes})
    log.info(
        f"[{fleet}] Clustering done: {len(all_routes)} trips on "
        f"{n_trucks}/{n_veh} trucks, {len(all_unserved)} unserved"
    )

    return {
        "routes"  : all_routes,
        "unserved": all_unserved,
        "nodes"   : [],
        "fleet"   : fleet,
    }


# ════════════════════════════════════════════════════════════
#  Trip renumbering
# ════════════════════════════════════════════════════════════

def _renumber_trips(routes: List[Dict]) -> List[Dict]:
    """
    After merging cluster results, a truck may have trip_number=1
    from two different cluster solves.  This pass assigns globally
    sequential numbers per truck: 1, 2, 3 … with no gaps.
    """
    routes.sort(key=lambda r: (r["truck_id"], r["trip_number"]))
    counter: Dict[str, int] = defaultdict(int)
    for r in routes:
        counter[r["truck_id"]] += 1
        r["trip_number"] = counter[r["truck_id"]]
        r["virtual_id"]  = f"{r['truck_id']}_T{r['trip_number']}"
    return routes


# ════════════════════════════════════════════════════════════
#  Unserved diagnosis
# ════════════════════════════════════════════════════════════

def _diagnose(
    nd      : Dict,
    vehicles: List[Dict],
    dist_mat: np.ndarray,
    nodes   : List[Dict],
    sched   : Dict,
) -> str:
    dkg    = nd["demand_kg"]
    dm3    = nd["demand_m3"]
    max_kg = max((v["cap_kg"] for v in vehicles), default=0)
    max_m3 = max((v["cap_m3"] for v in vehicles), default=0)

    if dkg > max_kg:
        return (f"Demand {dkg:.0f} kg exceeds the largest vehicle "
                f"({max_kg:.0f} kg). Split into multiple orders.")
    if dm3 > max_m3:
        return (f"Demand {dm3:.2f} m³ exceeds the largest vehicle "
                f"({max_m3:.2f} m³). Split into multiple orders.")

    try:
        ni = nodes.index(nd)
    except ValueError:
        ni = 0
    dist_km  = float(dist_mat[0][ni]) / 1000.0 if ni else 0.0
    tw_open  = nd.get("tw_open",  0)
    tw_close = nd.get("tw_close", 0)

    if dist_km > config.FAR_THRESHOLD_KM:
        return (f"Very far from depot ({dist_km:.0f} km). "
                f"Consider a dedicated run or removing this store.")

    if tw_open >= tw_close:
        return (f"Invalid time window ({tw_open/3600:.1f}h – "
                f"{tw_close/3600:.1f}h). Check store opening hours.")

    total_cap_kg = sum(v["cap_kg"] for v in vehicles) * config.MAX_TRIPS_PER_VEHICLE
    total_cap_m3 = sum(v["cap_m3"] for v in vehicles) * config.MAX_TRIPS_PER_VEHICLE
    total_dem_kg = sum(n2["demand_kg"] for n2 in nodes[1:])
    total_dem_m3 = sum(n2["demand_m3"] for n2 in nodes[1:])

    if total_dem_kg > total_cap_kg * 0.95:
        return (f"Fleet capacity exhausted "
                f"({total_dem_kg:.0f} kg demand vs {total_cap_kg:.0f} kg fleet). "
                f"Add more vehicles.")
    if total_dem_m3 > total_cap_m3 * 0.95:
        return (f"Fleet volume exhausted "
                f"({total_dem_m3:.1f} m³ demand vs {total_cap_m3:.1f} m³ fleet). "
                f"Add more vehicles.")

    return (
        f"Dropped by solver ({dist_km:.0f} km, "
        f"window {tw_open/3600:.1f}h–{tw_close/3600:.1f}h). "
        f"Try increasing solver time (300s+) or adding a vehicle."
    )


# ════════════════════════════════════════════════════════════
#  Public entry point
# ════════════════════════════════════════════════════════════

def solve(
    stores  : List[Dict],
    vehicles: List[Dict],
    dist_df,
    dur_df,
    mode    : str = "cheapest",
) -> Dict:
    """
    Solve CVRPTW for DRY and COLD fleets.

    Key behaviours (v6):
    ─────────────────────────────────────────────────────────
    1. No rural/urban split — all stores treated equally.
    2. config.CLUSTERING controls pre-clustering (see that flag's docs).
    3. Multi-trip is sequential: trip N+1 for truck X starts only after
       trip N for truck X returns to depot + RELOAD_TIME_SECONDS.
    4. Travel times are speed-adjusted: DRY (13:00, rush) gets slower
       times than COLD (03:00, clear roads) for the same distances.

    Args:
        stores:   list of store dicts from data_loader
        vehicles: list of vehicle dicts from data_loader
        dist_df:  NxN distance DataFrame (metres)
        dur_df:   NxN duration DataFrame  (minutes)
        mode:     "cheapest" | "fastest" | "shortest"

    Returns:
        {"DRY": {routes, unserved, nodes, fleet},
         "COLD": {routes, unserved, nodes, fleet}}
    """
    dry_v  = [v for v in vehicles if v["fleet"] == "DRY"]
    cold_v = [v for v in vehicles if v["fleet"] == "COLD"]

    depot_dry  = {**config.DEPOTS["Dry DC"],  "name": "Dry DC"}
    depot_cold = {**config.DEPOTS["Cold DC"], "name": "Cold DC"}

    t = config.MAX_SOLVER_TIME_SECONDS
    results: Dict = {}

    # ── DRY ──────────────────────────────────────────────────
    if dry_v:
        results["DRY"] = _solve_with_clustering(
            "DRY", depot_dry, stores, dry_v, dist_df, dur_df, mode, t
        )
    else:
        results["DRY"] = {
            "routes": [], "nodes": [], "fleet": "DRY",
            "unserved": [
                {"store": s, "reason": "No DRY vehicles configured.", "node": None}
                for s in stores if s.get("has_dry")
            ],
        }

    # ── COLD ─────────────────────────────────────────────────
    if cold_v:
        results["COLD"] = _solve_with_clustering(
            "COLD", depot_cold, stores, cold_v, dist_df, dur_df, mode, t
        )
    else:
        results["COLD"] = {
            "routes": [], "nodes": [], "fleet": "COLD",
            "unserved": [
                {"store": s, "reason": "No COLD vehicles configured.", "node": None}
                for s in stores if s.get("has_cold")
            ],
        }

    return results