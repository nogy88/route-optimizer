# # ============================================================
# #  clustering.py  —  Sweep-line pre-clustering for VRP
# #
# #  What it does:
# #    Divides stores into sectors (like slices of a pie from the
# #    depot) and assigns each sector to one vehicle.  This reduces
# #    the OR-Tools problem from one giant 600-node problem into
# #    many small ~12-node problems, each solved quickly.
# #
# #  Why sweep works:
# #    Delivery routes naturally form loops.  Stores that are in
# #    the same angular direction from the depot should share a
# #    truck.  Sweep mimics how an experienced dispatcher mentally
# #    divides a map into "truck territories".
# #
# #  Urban vs rural:
# #    Rural stores (far from depot) are separated BEFORE sweeping.
# #    They get clustered by geographic proximity instead of angle,
# #    because they often lie in specific corridors (e.g. the road
# #    to Dalanzadgad) rather than spreading evenly around the depot.
# #
# #  v5 additions:
# #    cluster_urban_stores() — new public function used by the
# #    improved _solve_with_clustering() in solver.py.  Returns
# #    plain lists of stores (no vehicle assignment) so that the
# #    caller can distribute vehicles proportionally.  Cluster count
# #    is hard-capped at config.MAX_CLUSTERS (default 10).
# # ============================================================

# import math
# from typing import Dict, List, Optional, Tuple

# import logging
# import config

# log = logging.getLogger(__name__)


# # ════════════════════════════════════════════════════════════
# #  Geometry helpers
# # ════════════════════════════════════════════════════════════

# def _haversine_km(lat1, lon1, lat2, lon2) -> float:
#     R = 6371.0
#     p1, p2 = math.radians(lat1), math.radians(lat2)
#     a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
#          + math.cos(p1) * math.cos(p2)
#          * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
#     return 2 * R * math.asin(math.sqrt(a))


# def _angle_from_depot(depot: Dict, store: Dict) -> float:
#     """
#     Compass angle of store from depot in radians (-π to π).
#     East = 0, North = π/2, West = ±π, South = -π/2.
#     """
#     return math.atan2(
#         store["lat"] - depot["lat"],
#         store["lon"] - depot["lon"],
#     )


# # ════════════════════════════════════════════════════════════
# #  Rural detection (by straight-line distance)
# # ════════════════════════════════════════════════════════════

# # Use straight-line distance as a cheap proxy for travel time.
# # 350 km straight-line ≈ ~7h drive on Mongolian roads.
# # Pulled from config so it can be tuned without code changes.
# _RURAL_DIST_KM: float = getattr(config, "RURAL_CLUSTERING_DIST_KM", 350.0)


# def _is_rural_by_distance(depot: Dict, store: Dict) -> bool:
#     return _haversine_km(
#         depot["lat"], depot["lon"],
#         store["lat"], store["lon"]
#     ) > _RURAL_DIST_KM


# # ════════════════════════════════════════════════════════════
# #  Cluster builder helpers
# # ════════════════════════════════════════════════════════════

# def _make_cluster(vehicle: Dict, stores: List[Dict]) -> Dict:
#     return {"vehicle": vehicle, "stores": stores}


# def _demand(store: Dict, fleet: str) -> Tuple[float, float]:
#     """Return (kg, m3) demand for the given fleet."""
#     if fleet == "DRY":
#         return store["dry_kg"], store["dry_cbm"]
#     return store["cold_kg"], store["cold_cbm"]


# def _effective_cap(vehicle: Dict) -> Tuple[float, float]:
#     """
#     Effective capacity per cluster = single vehicle × MAX_TRIPS.
#     The OR-Tools sub-solver handles multi-trip within each cluster,
#     so a cluster can carry up to (cap × max_trips) worth of goods.
#     """
#     t = config.MAX_TRIPS_PER_VEHICLE
#     return vehicle["cap_kg"] * t, vehicle["cap_m3"] * t


# # ════════════════════════════════════════════════════════════
# #  Urban sweep clustering
# # ════════════════════════════════════════════════════════════

# def _sweep_urban(
#     stores  : List[Dict],
#     vehicles: List[Dict],
#     fleet   : str,
# ) -> List[Dict]:
#     """
#     Assign urban stores to vehicles by angular sweep.

#     Algorithm:
#       1. Sort stores by their angle from the depot (0°–360°).
#       2. Walk through the sorted list, filling the current vehicle's
#          cluster until capacity would be exceeded.
#       3. When full, close the cluster, move to the next vehicle.
#       4. If all vehicles are used before all stores are assigned,
#          overflow stores are added to the last vehicle's cluster
#          (OR-Tools multi-trip will handle the extra load).

#     Returns list of {vehicle, stores} dicts.
#     """
#     if not stores or not vehicles:
#         return []

#     # Sort by angle so geographically adjacent stores end up together
#     stores_sorted = sorted(stores, key=lambda s: s.get("_angle", 0.0))

#     clusters: List[Dict]  = []
#     v_idx     = 0          # current vehicle index
#     cur_stores: List[Dict] = []
#     cur_kg    = 0.0
#     cur_m3    = 0.0
#     n_veh     = len(vehicles)

#     for s in stores_sorted:
#         dem_kg, dem_m3 = _demand(s, fleet)
#         veh            = vehicles[min(v_idx, n_veh - 1)]
#         eff_kg, eff_m3 = _effective_cap(veh)

#         # Try to close the current cluster and open a new one.
#         # Only do this if there are still unused vehicles available
#         # AND the current cluster already has something in it.
#         would_overflow = (cur_kg + dem_kg > eff_kg or
#                           cur_m3 + dem_m3 > eff_m3)
#         can_open_new   = v_idx < n_veh - 1   # still have unused vehicles
#         should_split   = would_overflow and can_open_new and cur_stores

#         if should_split:
#             # Close current cluster
#             clusters.append(_make_cluster(veh, cur_stores))
#             v_idx  += 1
#             cur_stores = []
#             cur_kg     = 0.0
#             cur_m3     = 0.0

#         cur_stores.append(s)
#         cur_kg += dem_kg
#         cur_m3 += dem_m3

#     # Close the last cluster
#     if cur_stores:
#         clusters.append(_make_cluster(vehicles[min(v_idx, n_veh - 1)], cur_stores))

#     return clusters


# # ════════════════════════════════════════════════════════════
# #  Rural proximity clustering
# # ════════════════════════════════════════════════════════════

# def _cluster_rural(
#     stores  : List[Dict],
#     vehicles: List[Dict],
#     fleet   : str,
# ) -> List[Dict]:
#     """
#     Group rural stores by geographic proximity (greedy nearest-neighbour).

#     Rural stores are sparse and scattered along specific roads, so
#     angular sweep doesn't work well for them.  Instead:
#       1. Pick the unassigned store farthest from the depot as a seed.
#       2. Greedily add the nearest unassigned store to that cluster
#          until the vehicle's effective capacity is full.
#       3. Repeat until all rural stores are assigned.
#       4. If vehicles run out, add excess stores to the last cluster.

#     Returns list of {vehicle, stores} dicts.
#     """
#     if not stores or not vehicles:
#         return []

#     unassigned = list(stores)
#     clusters: List[Dict] = []
#     v_idx = 0
#     n_veh = len(vehicles)

#     while unassigned:
#         veh            = vehicles[min(v_idx, n_veh - 1)]
#         eff_kg, eff_m3 = _effective_cap(veh)
#         can_open_new   = v_idx < n_veh - 1

#         # Seed: farthest unassigned store (by straight-line from depot)
#         # Use the pre-computed distance if available
#         seed = max(unassigned, key=lambda s: s.get("_dist_km", 0.0))
#         unassigned.remove(seed)

#         cur_stores = [seed]
#         dem_kg, dem_m3 = _demand(seed, fleet)
#         cur_kg, cur_m3 = dem_kg, dem_m3

#         # Greedily add nearest stores until cluster is full
#         while unassigned:
#             # Find nearest unassigned store to current cluster centroid
#             c_lat = sum(s["lat"] for s in cur_stores) / len(cur_stores)
#             c_lon = sum(s["lon"] for s in cur_stores) / len(cur_stores)
#             nearest = min(
#                 unassigned,
#                 key=lambda s: _haversine_km(c_lat, c_lon, s["lat"], s["lon"])
#             )
#             nk, nm = _demand(nearest, fleet)

#             fits_in_cluster   = (cur_kg + nk <= eff_kg and
#                                  cur_m3 + nm <= eff_m3)
#             should_add        = fits_in_cluster or not can_open_new

#             if should_add:
#                 cur_stores.append(nearest)
#                 unassigned.remove(nearest)
#                 cur_kg += nk
#                 cur_m3 += nm
#             else:
#                 # Cluster is full and we have more vehicles; stop here
#                 break

#         clusters.append(_make_cluster(veh, cur_stores))

#         # Advance to next vehicle for next cluster, but only if we have more
#         if can_open_new:
#             v_idx += 1

#     return clusters


# # ════════════════════════════════════════════════════════════
# #  NEW: cluster_urban_stores  (used by improved solver.py)
# # ════════════════════════════════════════════════════════════

# def cluster_urban_stores(
#     stores      : List[Dict],
#     depot       : Dict,
#     vehicles    : List[Dict],
#     fleet       : str,
#     max_clusters: Optional[int] = None,
# ) -> Tuple[List[List[Dict]], List[Dict]]:
#     """
#     Split fleet-eligible stores into geographic urban groups + rural stores.

#     Unlike the legacy sweep_cluster(), this function does NOT assign a
#     specific vehicle to each cluster.  Vehicles are distributed later by
#     _solve_with_clustering() so each cluster gets a proportional share of
#     the whole fleet, preventing the single-vehicle capacity-isolation
#     problem.

#     Args:
#         stores:       All stores (any fleet).
#         depot:        Depot dict with 'lat', 'lon', 'name'.
#         vehicles:     Fleet vehicles (already filtered to this fleet).
#         fleet:        "DRY" or "COLD".
#         max_clusters: Hard ceiling on urban cluster count.  Defaults to
#                       min(len(vehicles), config.MAX_CLUSTERS).

#     Returns:
#         urban_groups  — list of store-lists (≤ max_clusters entries).
#         rural_stores  — flat list of rural stores to solve in one batch.

#     Raises:
#         Nothing.  If there are no eligible stores, returns ([], []).
#     """
#     # ── 1. Filter to fleet-eligible stores ───────────────────
#     eligible = [
#         s for s in stores
#         if (s["has_dry"] if fleet == "DRY" else s["has_cold"])
#     ]
#     if not eligible or not vehicles:
#         return [], []

#     # ── 2. Annotate with angle + distance from depot ─────────
#     for s in eligible:
#         s["_angle"]   = _angle_from_depot(depot, s)
#         s["_dist_km"] = _haversine_km(
#             depot["lat"], depot["lon"], s["lat"], s["lon"]
#         )

#     # ── 3. Split urban / rural ────────────────────────────────
#     urban  = [s for s in eligible if not _is_rural_by_distance(depot, s)]
#     rural  = [s for s in eligible if     _is_rural_by_distance(depot, s)]

#     n_veh  = len(vehicles)

#     # ── 4. Determine cluster ceiling ─────────────────────────
#     cfg_max = getattr(config, "MAX_CLUSTERS", n_veh)
#     if max_clusters is None:
#         max_clusters = min(n_veh, cfg_max)
#     else:
#         max_clusters = min(max_clusters, n_veh, cfg_max)
#     max_clusters = max(1, max_clusters)

#     log.info(
#         f"[{fleet}] cluster_urban_stores: "
#         f"{len(urban)} urban + {len(rural)} rural eligible stores, "
#         f"{n_veh} vehicles, target ≤{max_clusters} clusters"
#     )

#     # ── 5. Sweep urban into ≤ max_clusters groups ─────────────
#     # We use dummy vehicles just to drive the sweep algorithm's
#     # capacity check — one dummy per target cluster, each sized as
#     # the average vehicle capacity.  This prevents the sweep from
#     # creating more clusters than we want.
#     if urban:
#         avg_kg = sum(v["cap_kg"] for v in vehicles) / n_veh
#         avg_m3 = sum(v["cap_m3"] for v in vehicles) / n_veh

#         # Build exactly max_clusters dummy vehicles
#         dummy_vehicles = [
#             {
#                 **vehicles[0],  # copy fuel_cost etc. (not used here)
#                 "truck_id": f"_dummy_{i}",
#                 "cap_kg"  : avg_kg,
#                 "cap_m3"  : avg_m3,
#             }
#             for i in range(max_clusters)
#         ]

#         sweep_results = _sweep_urban(urban, dummy_vehicles, fleet)
#         urban_groups  = [cl["stores"] for cl in sweep_results]
#     else:
#         urban_groups = []

#     log.info(
#         f"[{fleet}] → {len(urban_groups)} urban clusters, "
#         f"{len(rural)} rural stores"
#     )

#     # ── 6. Clean up temporary annotation keys ─────────────────
#     for s in eligible:
#         s.pop("_angle",   None)
#         s.pop("_dist_km", None)

#     return urban_groups, rural


# # ════════════════════════════════════════════════════════════
# #  Legacy public API  (kept for backward compatibility)
# # ════════════════════════════════════════════════════════════

# def sweep_cluster(
#     stores  : List[Dict],
#     depot   : Dict,
#     vehicles: List[Dict],
#     fleet   : str,
# ) -> List[Dict]:
#     """
#     Legacy entry point — still used if anything calls it directly.

#     Divide stores into vehicle-sized clusters using sweep + proximity.

#     Returns:
#         list of {"vehicle": <vehicle_dict>, "stores": [<store_dict>, ...]}

#     Each cluster will be solved independently by _solve_fleet() with
#     the assigned vehicle + MAX_TRIPS_PER_VEHICLE trip slots.

#     Steps:
#         1. Filter to eligible stores for this fleet.
#         2. Annotate each store with its angle and distance from depot.
#         3. Separate urban (≤ RURAL_CLUSTERING_DIST_KM) from rural stores.
#         4. Apply sweep to urban stores, proximity to rural stores.
#         5. Split the vehicle list: roughly proportion to store counts.
#         6. Merge and return.
#     """
#     # ── 1. Filter ──────────────────────────────────────────────
#     eligible = [
#         s for s in stores
#         if (s["has_dry"] if fleet == "DRY" else s["has_cold"])
#     ]
#     if not eligible or not vehicles:
#         return []

#     # ── 2. Annotate ────────────────────────────────────────────
#     for s in eligible:
#         s["_angle"]   = _angle_from_depot(depot, s)
#         s["_dist_km"] = _haversine_km(
#             depot["lat"], depot["lon"], s["lat"], s["lon"]
#         )

#     # ── 3. Split urban / rural ─────────────────────────────────
#     urban  = [s for s in eligible if not _is_rural_by_distance(depot, s)]
#     rural  = [s for s in eligible if     _is_rural_by_distance(depot, s)]

#     log.info(f"[{fleet}] Clustering: {len(urban)} urban + {len(rural)} rural stores, "
#              f"{len(vehicles)} vehicles")

#     n_total = len(eligible)
#     n_veh   = len(vehicles)

#     # ── 4. Split the vehicle pool proportionally ───────────────
#     # Urban gets the majority; rural gets at least 1 if there are rural stores.
#     if rural and n_veh > 1:
#         n_rural_veh = max(1, round(n_veh * len(rural) / n_total))
#         n_rural_veh = min(n_rural_veh, n_veh - 1)  # leave at least 1 for urban
#     else:
#         n_rural_veh = 0

#     n_urban_veh   = n_veh - n_rural_veh
#     urban_vehicles = vehicles[:n_urban_veh]
#     rural_vehicles = vehicles[n_urban_veh:]

#     # ── 5. Cluster each group ──────────────────────────────────
#     urban_clusters = _sweep_urban(urban, urban_vehicles, fleet)
#     rural_clusters = _cluster_rural(rural, rural_vehicles, fleet) if rural else []

#     all_clusters = urban_clusters + rural_clusters

#     log.info(f"[{fleet}] → {len(urban_clusters)} urban clusters, "
#              f"{len(rural_clusters)} rural clusters")

#     # ── 6. Clean up temporary annotation keys ─────────────────
#     for s in eligible:
#         s.pop("_angle",   None)
#         s.pop("_dist_km", None)

#     return all_clusters