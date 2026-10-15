"""Dynamic flood-aware Dijkstra navigation across Fastest, Safest, and Balanced modes."""

import heapq
import json
import math
import os
from typing import Any, Dict, List, Optional, Set, Tuple
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from backend.app.config import settings
from backend.app.core.exceptions import RoutingException
from backend.app.schemas.routing import (
    RouteRequestSchema,
    RouteResponseSchema,
    RouteSegmentSchema,
    SingleRouteSchema,
)
from backend.app.services.geojson_loader import geojson_loader

LUCKNOW_COS_LAT = math.cos(math.radians(26.85))  # ~0.89218


def geographic_distance_m(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """Calculates ground distance in meters with latitude correction."""
    dlon_m = (p1[0] - p2[0]) * 111000.0 * LUCKNOW_COS_LAT
    dlat_m = (p1[1] - p2[1]) * 111000.0
    return math.hypot(dlon_m, dlat_m)


class RoutingService:
    """Computes flood-safe vehicle routes with dynamic depth overlays and clearance barriers."""

    def __init__(self):
        self._graphs: Dict[str, nx.Graph] = {}
        self._kdtrees: Dict[str, cKDTree] = {}
        self._node_coords: Dict[str, Dict[str, Tuple[float, float]]] = {}
        self._node_ids: Dict[str, List[str]] = {}
        self._build_graphs()

    def _build_graphs(self) -> None:
        """Constructs bidirectional street network graphs for supported zones."""
        for zone in ["pilot", "validation"]:
            z_dir = settings.SPATIAL_VALIDATION_DIR if zone == "validation" else settings.SPATIAL_PILOT_DIR
            osm_roads_path = os.path.join(z_dir, "osm_roads.geojson")
            road_edges_path = os.path.join(z_dir, "road_edges.geojson")
            edges_path = osm_roads_path if os.path.exists(osm_roads_path) else (
                road_edges_path if os.path.exists(road_edges_path) else os.path.join(z_dir, "drainage_edges.geojson")
            )

            if not os.path.exists(edges_path):
                continue

            with open(edges_path, "r", encoding="utf-8") as f:
                edges_gj = json.load(f)

            node_map: Dict[str, Tuple[float, float]] = {}
            G = nx.DiGraph()

            for feat in edges_gj.get("features", []):
                props = feat.get("properties", {})
                u = str(props.get("from", props.get("u", "")))
                v = str(props.get("to", props.get("v", "")))
                if not u or not v or u == v:
                    continue

                coords = feat.get("geometry", {}).get("coordinates", [])
                if not coords or len(coords) < 2:
                    continue

                node_map[u] = tuple(coords[0])
                node_map[v] = tuple(coords[-1])

                length_m = float(props.get("length_m", 50.0))
                hway = str(props.get("highway", "residential"))
                name = str(props.get("name", "") or "")
                oneway = bool(props.get("oneway", False))

                speed_kmh = 55.0 if "primary" in hway or "trunk" in hway else (
                    40.0 if "secondary" in hway else (30.0 if "tertiary" in hway else 18.0)
                )
                base_time_s = length_m / max(speed_kmh / 3.6, 1.0)

                G.add_edge(
                    u, v,
                    length_m=length_m,
                    highway=hway,
                    name=name if name else hway.replace("_", " ").title(),
                    speed_kmh=speed_kmh,
                    base_time_s=base_time_s,
                    geometry=coords,
                    oneway=oneway,
                )

                if not oneway:
                    rev_coords = list(reversed(coords))
                    G.add_edge(
                        v, u,
                        length_m=length_m,
                        highway=hway,
                        name=name if name else hway.replace("_", " ").title(),
                        speed_kmh=speed_kmh,
                        base_time_s=base_time_s,
                        geometry=rev_coords,
                        oneway=False,
                    )

            node_ids = list(node_map.keys())
            coords_arr = [node_map[nid] for nid in node_ids]
            self._node_coords[zone] = node_map
            self._node_ids[zone] = node_ids
            if coords_arr:
                self._kdtrees[zone] = cKDTree(np.array(coords_arr))

            # Bridge weakly connected components if present
            while nx.number_weakly_connected_components(G) > 1:
                comps = list(nx.weakly_connected_components(G))
                c1, c2 = comps[0], comps[1]
                best_u, best_v, min_d = None, None, float("inf")
                for u in list(c1)[:80]:
                    for v in list(c2)[:80]:
                        d = geographic_distance_m(node_map[u], node_map[v])
                        if d < min_d:
                            min_d = d
                            best_u = u
                            best_v = v
                if best_u and best_v:
                    G.add_edge(
                        best_u, best_v,
                        length_m=round(min_d, 1),
                        highway="residential",
                        name="Street Connection",
                        speed_kmh=15.0,
                        base_time_s=min_d / (15.0 / 3.6),
                        geometry=[list(node_map[best_u]), list(node_map[best_v])],
                        oneway=False,
                    )
                    G.add_edge(
                        best_v, best_u,
                        length_m=round(min_d, 1),
                        highway="residential",
                        name="Street Connection",
                        speed_kmh=15.0,
                        base_time_s=min_d / (15.0 / 3.6),
                        geometry=[list(node_map[best_v]), list(node_map[best_u])],
                        oneway=False,
                    )
                else:
                    break

            self._graphs[zone] = G

    def find_nearest_node(self, lon: float, lat: float, zone: str = "pilot") -> Tuple[str, float]:
        tree = self._kdtrees.get(zone)
        G = self._graphs.get(zone)
        node_coords = self._node_coords.get(zone, {})
        if tree is None or G is None:
            raise RoutingException(f"Spatial index missing for zone '{zone}'.")

        p = (lon, lat)
        k_val = min(35, len(self._node_ids[zone]))
        dists, idxs = tree.query([lon, lat], k=k_val)
        if isinstance(idxs, (int, np.integer)):
            idxs = [idxs]

        candidate_nodes = set([self._node_ids[zone][i] for i in idxs])
        best_dist = float("inf")
        best_node = self._node_ids[zone][idxs[0]]

        for u in candidate_nodes:
            for v in G.neighbors(u):
                u_c = node_coords.get(u)
                v_c = node_coords.get(v)
                if not u_c or not v_c:
                    continue
                geom = G.edges[u, v].get("geometry") or [u_c, v_c]
                for i in range(len(geom) - 1):
                    ax, ay = geom[i]
                    bx, by = geom[i + 1]
                    dx, dy = bx - ax, by - ay
                    if dx == 0 and dy == 0:
                        dist_m = geographic_distance_m(p, (ax, ay))
                        proj_pt = (ax, ay)
                    else:
                        t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
                        proj_pt = (ax + t * dx, ay + t * dy)
                        dist_m = geographic_distance_m(p, proj_pt)

                    if dist_m < best_dist:
                        best_dist = dist_m
                        d_u = geographic_distance_m(proj_pt, u_c)
                        d_v = geographic_distance_m(proj_pt, v_c)
                        best_node = v if d_v <= d_u else u

        return best_node, best_dist

    def compute_safe_routes(self, req: RouteRequestSchema) -> RouteResponseSchema:
        from backend.app.services.file_index import file_index

        zone = file_index.validate_zone(req.zone)
        G = self._graphs.get(zone)
        if G is None or G.number_of_nodes() == 0:
            raise RoutingException(f"Road network for zone '{zone}' is unavailable.")

        if req.from_node and req.from_node in G:
            start_node = req.from_node
            start_lon, start_lat = self._node_coords[zone][start_node]
        elif req.from_lat is not None and req.from_lon is not None:
            start_node, _ = self.find_nearest_node(req.from_lon, req.from_lat, zone=zone)
            start_lon, start_lat = req.from_lon, req.from_lat
        else:
            raise RoutingException("Origin coordinates (from_lat, from_lon) are required.")

        if req.to_node and req.to_node in G:
            end_node = req.to_node
            end_lon, end_lat = self._node_coords[zone][end_node]
        elif req.to_lat is not None and req.to_lon is not None:
            end_node, _ = self.find_nearest_node(req.to_lon, req.to_lat, zone=zone)
            end_lon, end_lat = req.to_lon, req.to_lat
        else:
            raise RoutingException("Destination coordinates (to_lat, to_lon) are required.")

        vtype = req.vehicle_type.lower()
        clearance_cm = req.custom_clearance_cm or settings.VEHICLE_CLEARANCES_CM.get(
            vtype, settings.VEHICLE_CLEARANCES_CM["car"]
        )

        edge_depths: Dict[Tuple[str, str], float] = {}
        try:
            nodes_fc = geojson_loader.load_layer(zone, req.scenario, req.t, "nodes")
            sim_coords = []
            sim_depths = []
            for f in nodes_fc.get("features", []):
                lon, lat = f["geometry"]["coordinates"]
                d = float(f["properties"].get("depth_cm", 0.0))
                sim_coords.append([lon, lat])
                sim_depths.append(d)

            if sim_coords:
                sim_coords_scaled = [[c[0] * LUCKNOW_COS_LAT, c[1]] for c in sim_coords]
                sim_tree = cKDTree(np.array(sim_coords_scaled))
                sim_depths_arr = np.array(sim_depths)

                for u, v, data in G.edges(data=True):
                    u_c = self._node_coords[zone].get(u)
                    v_c = self._node_coords[zone].get(v)
                    if u_c and v_c:
                        mid_pt_scaled = [((u_c[0] + v_c[0]) / 2.0) * LUCKNOW_COS_LAT, (u_c[1] + v_c[1]) / 2.0]
                        dist, idx = sim_tree.query(mid_pt_scaled)
                        if dist * 111000.0 <= 65.0:
                            d_val = float(sim_depths_arr[idx])
                            edge_depths[(u, v)] = d_val
                            edge_depths[(v, u)] = d_val
                        else:
                            edge_depths[(u, v)] = 0.0
                            edge_depths[(v, u)] = 0.0
        except Exception:
            pass

        orig_pt = (start_lon, start_lat)
        dest_pt = (end_lon, end_lat)

        route_results: Dict[str, SingleRouteSchema] = {}

        # Fastest: time-optimal path
        r_fastest = self._run_dijkstra(
            G=G, start=start_node, end=end_node, mode="fastest",
            edge_depths=edge_depths, clearance_cm=clearance_cm, zone=zone,
            orig_coord=orig_pt, dest_coord=dest_pt,
        )
        route_results["fastest"] = r_fastest
        p1_major_edges = {
            (s.u, s.v): 3.0 for s in r_fastest.segments
            if "primary" in s.highway_class or "secondary" in s.highway_class or "trunk" in s.highway_class
        }

        # Safest: flood-avoiding corridor
        r_safest = self._run_dijkstra(
            G=G, start=start_node, end=end_node, mode="safest",
            edge_depths=edge_depths, clearance_cm=clearance_cm, zone=zone,
            orig_coord=orig_pt, dest_coord=dest_pt,
        )
        if [s.u for s in r_safest.segments] == [s.u for s in r_fastest.segments]:
            r_safest_alt = self._run_dijkstra(
                G=G, start=start_node, end=end_node, mode="safest",
                edge_depths=edge_depths, clearance_cm=clearance_cm, zone=zone,
                avoid_edges=p1_major_edges,
                orig_coord=orig_pt, dest_coord=dest_pt,
            )
            if r_safest_alt.max_flood_depth_cm <= r_fastest.max_flood_depth_cm + 0.1:
                r_safest = r_safest_alt
        route_results["safest"] = r_safest

        # Balanced: tradeoff corridor
        r_balanced = self._run_dijkstra(
            G=G, start=start_node, end=end_node, mode="balanced",
            edge_depths=edge_depths, clearance_cm=clearance_cm, zone=zone,
            orig_coord=orig_pt, dest_coord=dest_pt,
        )
        if [s.u for s in r_balanced.segments] == [s.u for s in r_fastest.segments] and [s.u for s in r_safest.segments] != [s.u for s in r_fastest.segments]:
            r_balanced_alt = self._run_dijkstra(
                G=G, start=start_node, end=end_node, mode="balanced",
                edge_depths=edge_depths, clearance_cm=clearance_cm, zone=zone,
                avoid_edges=p1_major_edges,
                orig_coord=orig_pt, dest_coord=dest_pt,
            )
            if r_balanced_alt.total_travel_time_minutes <= max(r_fastest.total_travel_time_minutes, r_safest.total_travel_time_minutes) * 1.15 and r_balanced_alt.max_flood_depth_cm <= r_fastest.max_flood_depth_cm + 0.1:
                r_balanced = r_balanced_alt
            else:
                r_balanced = r_safest
        route_results["balanced"] = r_balanced

        modes_to_calc = ["fastest", "safest", "balanced"]
        comparison = []
        for m in modes_to_calc:
            r = route_results[m]
            comparison.append({
                "mode": m,
                "label": r.mode_label,
                "travel_time_min": r.total_travel_time_minutes,
                "distance_km": r.total_distance_km,
                "max_flood_depth_cm": r.max_flood_depth_cm,
                "status": r.status,
                "recommendation": r.recommendation,
                "flooded_segments": r.flooded_segments_count,
            })

        return RouteResponseSchema(
            zone=zone,
            scenario_id=req.scenario.upper(),
            timestep_minutes=req.t,
            vehicle_type=vtype,
            ground_clearance_cm=clearance_cm,
            start_node=start_node,
            end_node=end_node,
            start_coordinates=[round(start_lon, 6), round(start_lat, 6)],
            end_coordinates=[round(end_lon, 6), round(end_lat, 6)],
            selected_mode=req.mode if req.mode != "all" else "balanced",
            routes=route_results,
            comparison=comparison,
        )

    def _run_dijkstra(
        self,
        G: nx.Graph,
        start: str,
        end: str,
        mode: str,
        edge_depths: Dict[Tuple[str, str], float],
        clearance_cm: float,
        zone: str,
        avoid_edges: Optional[Dict[Tuple[str, str], float]] = None,
        orig_coord: Optional[Tuple[float, float]] = None,
        dest_coord: Optional[Tuple[float, float]] = None,
    ) -> SingleRouteSchema:
        pq = [(0.0, start, [start])]
        best_cost: Dict[Tuple[str, Optional[str]], float] = {(start, None): 0.0}
        path_nodes: Optional[List[str]] = None

        while pq:
            cost, u, path = heapq.heappop(pq)
            if u == end:
                path_nodes = path
                break

            p_node = path[-2] if len(path) >= 2 else None
            state_key = (u, p_node)
            if state_key in best_cost and cost > best_cost[state_key]:
                continue
            best_cost[state_key] = cost

            u_coord = self._node_coords[zone].get(u)
            p_coord = self._node_coords[zone].get(p_node) if p_node else None

            for v in G.neighbors(u):
                if v == p_node or (len(path) >= 3 and v in path[-3:]):
                    continue

                edata = G.edges[u, v]
                length_m = edata.get("length_m", 50.0)
                base_time_s = edata.get("base_time_s", 5.0)
                depth_cm = edge_depths.get((u, v), 0.0)
                hway = str(edata.get("highway", "residential")).lower()
                name = str(edata.get("name", "") or "")

                turn_cost = 0.0
                if p_coord is not None and u_coord is not None:
                    v_coord = self._node_coords[zone].get(v)
                    if v_coord is not None:
                        v1 = (u_coord[0] - p_coord[0], u_coord[1] - p_coord[1])
                        v2 = (v_coord[0] - u_coord[0], v_coord[1] - u_coord[1])
                        n1 = math.hypot(v1[0], v1[1])
                        n2 = math.hypot(v2[0], v2[1])
                        if n1 > 1e-6 and n2 > 1e-6:
                            dot = v1[0] * v2[0] + v1[1] * v2[1]
                            cos_a = max(-1.0, min(1.0, dot / (n1 * n2)))
                            deg = math.degrees(math.acos(cos_a))
                            prev_edge_name = str(G.edges[p_node, u].get("name", "") or "")
                            is_same_road = bool(name and name == prev_edge_name)

                            if deg > 90.0:
                                turn_cost = 45.0 + 35.0 * ((deg - 90.0) / 90.0) if not is_same_road else 20.0
                            elif deg > 45.0:
                                turn_cost = 10.0 * (deg / 90.0) if not is_same_road else 2.0

                if "primary" in hway or "trunk" in hway:
                    road_priority = 1.0
                elif "secondary" in hway:
                    road_priority = 1.15
                elif "tertiary" in hway:
                    road_priority = 1.35
                else:
                    road_priority = 2.2

                if mode == "fastest":
                    if depth_cm > clearance_cm:
                        edge_cost = 50000.0 + base_time_s * 20.0
                    elif depth_cm > 0.5:
                        slowdown = 1.0 + 3.0 * ((depth_cm / max(clearance_cm, 1.0)) ** 1.5)
                        edge_cost = (base_time_s * slowdown) * road_priority + turn_cost
                    else:
                        edge_cost = base_time_s * road_priority + turn_cost
                elif mode == "safest":
                    if depth_cm > clearance_cm:
                        edge_cost = 100000.0 + length_m * 100.0
                    elif depth_cm >= 1.0:
                        flood_penalty = 150.0 * ((depth_cm / 5.0) ** 2.2) + depth_cm * 25.0 + length_m * 1.5
                        edge_cost = (base_time_s + flood_penalty) * road_priority + turn_cost
                    else:
                        edge_cost = base_time_s * road_priority + turn_cost
                else:  # balanced
                    if depth_cm > clearance_cm:
                        edge_cost = 75000.0 + base_time_s * 30.0
                    elif depth_cm >= 3.0:
                        flood_penalty = 25.0 * ((depth_cm / 5.0) ** 1.6) + depth_cm * 6.0
                        edge_cost = (base_time_s + flood_penalty) * road_priority + turn_cost
                    elif depth_cm > 0.0:
                        edge_cost = (base_time_s + depth_cm * 1.5) * road_priority + turn_cost
                    else:
                        edge_cost = base_time_s * road_priority + turn_cost

                if avoid_edges:
                    edge_cost *= avoid_edges.get((u, v), avoid_edges.get((v, u), 1.0))

                new_cost = cost + edge_cost
                next_state = (v, u)
                if new_cost < best_cost.get(next_state, float("inf")):
                    best_cost[next_state] = new_cost
                    heapq.heappush(pq, (new_cost, v, path + [v]))

        if path_nodes is None:
            try:
                path_nodes = nx.shortest_path(G, start, end, weight="length_m")
            except Exception:
                path_nodes = [start, end]

        if len(path_nodes) >= 4:
            smoothed = [path_nodes[0]]
            k = 1
            while k < len(path_nodes) - 1:
                prev_n = smoothed[-1]
                curr_n = path_nodes[k]
                next_n = path_nodes[k + 1]

                if G.has_edge(prev_n, next_n):
                    d_direct = G.edges[prev_n, next_n]
                    d_curr = G.edges[prev_n, curr_n]
                    if d_direct.get("name") and d_direct.get("name") == d_curr.get("name"):
                        smoothed.append(next_n)
                        k += 2
                        continue

                smoothed.append(curr_n)
                k += 1
            if k == len(path_nodes) - 1:
                smoothed.append(path_nodes[-1])
            path_nodes = smoothed

        segments = []
        coords_path = []
        tot_dist_m = 0.0
        tot_time_s = 0.0
        max_depth = 0.0
        depths_list = []
        flooded_count = 0
        impassable_count = 0

        for i in range(len(path_nodes) - 1):
            u = path_nodes[i]
            v = path_nodes[i + 1]
            edata = G.edges.get((u, v), {})
            length_m = edata.get("length_m", 50.0)
            base_time_s = edata.get("base_time_s", 5.0)
            name = edata.get("name", "Street")
            hway = edata.get("highway", "residential")
            depth_cm = edge_depths.get((u, v), 0.0)

            actual_time_s = self._calc_slowdown_time(base_time_s, depth_cm, clearance_cm)

            tot_dist_m += length_m
            tot_time_s += actual_time_s
            max_depth = max(max_depth, depth_cm)
            depths_list.append(depth_cm)

            if depth_cm > clearance_cm:
                passability = "impassable"
                impassable_count += 1
                flooded_count += 1
            elif depth_cm >= 5.0:
                passability = "caution"
                flooded_count += 1
            else:
                passability = "passable"

            segments.append(
                RouteSegmentSchema(
                    u=u,
                    v=v,
                    road_name=name,
                    highway_class=hway,
                    length_m=round(length_m, 1),
                    flood_depth_cm=round(depth_cm, 1),
                    passability=passability,
                    travel_time_s=round(actual_time_s, 1),
                )
            )

            u_c = self._node_coords[zone].get(u, (80.94, 26.85))
            v_c = self._node_coords[zone].get(v, (80.94, 26.85))
            edge_geom = edata.get("geometry")

            if edge_geom and len(edge_geom) >= 2:
                d_forward = math.dist(edge_geom[0], u_c) + math.dist(edge_geom[-1], v_c)
                d_reverse = math.dist(edge_geom[-1], u_c) + math.dist(edge_geom[0], v_c)
                pts = [list(p) for p in (edge_geom if d_forward <= d_reverse else list(reversed(edge_geom)))]

                pts[0] = [u_c[0], u_c[1]]
                pts[-1] = [v_c[0], v_c[1]]

                if not coords_path:
                    coords_path.append([round(pts[0][0], 6), round(pts[0][1], 6)])
                for pt in pts[1:]:
                    coords_path.append([round(pt[0], 6), round(pt[1], 6)])
            else:
                if not coords_path:
                    coords_path.append([round(u_c[0], 6), round(u_c[1], 6)])
                coords_path.append([round(v_c[0], 6), round(v_c[1], 6)])

        if len(coords_path) >= 3:
            cleaned = list(coords_path)
            changed = True
            iteration = 0
            while changed and iteration < 10:
                iteration += 1
                changed = False
                new_coords = [cleaned[0]]
                ci = 1
                while ci < len(cleaned) - 1:
                    p1, p2, p3 = new_coords[-1], cleaned[ci], cleaned[ci + 1]
                    vx1 = (p2[0] - p1[0]) * 111000.0 * LUCKNOW_COS_LAT
                    vy1 = (p2[1] - p1[1]) * 111000.0
                    vx2 = (p3[0] - p2[0]) * 111000.0 * LUCKNOW_COS_LAT
                    vy2 = (p3[1] - p2[1]) * 111000.0
                    n1 = math.hypot(vx1, vy1)
                    n2 = math.hypot(vx2, vy2)

                    if n1 > 1e-3 and n2 > 1e-3:
                        dot_val = (vx1 * vx2 + vy1 * vy2) / (n1 * n2)
                        cos_a = max(-1.0, min(1.0, dot_val))
                        turn_deg = math.degrees(math.acos(cos_a))

                        if turn_deg > 125.0 and (n1 < 35.0 or n2 < 35.0):
                            changed = True
                            ci += 1
                            continue
                    new_coords.append(p2)
                    ci += 1
                if ci == len(cleaned) - 1:
                    new_coords.append(cleaned[-1])
                cleaned = new_coords
            coords_path = cleaned

        if orig_coord and coords_path:
            if geographic_distance_m(orig_coord, coords_path[0]) > 1.5:
                coords_path.insert(0, [round(orig_coord[0], 6), round(orig_coord[1], 6)])
        if dest_coord and coords_path:
            if geographic_distance_m(dest_coord, coords_path[-1]) > 1.5:
                coords_path.append([round(dest_coord[0], 6), round(dest_coord[1], 6)])

        mean_depth = float(np.mean(depths_list)) if depths_list else 0.0
        is_passable = impassable_count == 0

        if max_depth > clearance_cm:
            status = "IMPASSABLE"
            rec = "Avoid"
        elif max_depth >= 5.0:
            status = "CAUTION"
            rec = "Caution"
        else:
            status = "SAFE"
            rec = "Recommended"

        labels = {
            "fastest": "Fastest Route",
            "safest": "Safest Route",
            "balanced": "Balanced Route",
        }

        geojson_feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords_path,
            },
            "properties": {
                "mode": mode,
                "status": status,
                "recommendation": rec,
                "distance_km": round(tot_dist_m / 1000.0, 2),
                "travel_time_min": round(tot_time_s / 60.0, 1),
                "max_flood_depth_cm": round(max_depth, 1),
                "flooded_segments": flooded_count,
            },
        }

        return SingleRouteSchema(
            mode=mode,
            mode_label=labels.get(mode, mode.title()),
            is_passable=is_passable,
            status=status,
            recommendation=rec,
            total_distance_km=round(tot_dist_m / 1000.0, 2),
            total_travel_time_minutes=round(tot_time_s / 60.0, 1),
            max_flood_depth_cm=round(max_depth, 1),
            mean_flood_depth_cm=round(mean_depth, 1),
            flooded_segments_count=flooded_count,
            impassable_segments_count=impassable_count,
            geojson=geojson_feature,
            segments=segments,
        )

    def _calc_slowdown_time(self, base_time_s: float, depth_cm: float, clearance_cm: float) -> float:
        if depth_cm <= 2.0:
            return base_time_s
        elif depth_cm <= 5.0:
            return base_time_s * 1.25
        elif depth_cm <= 15.0:
            return base_time_s * 2.50
        elif depth_cm <= clearance_cm:
            return base_time_s * 6.00
        else:
            return base_time_s * 15.00


routing_service = RoutingService()
