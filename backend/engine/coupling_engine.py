"""Hydraulic coupling engine for urban stormwater routing and street flood nowcasting."""

from dataclasses import dataclass, field
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import networkx as nx
import rasterio

from .rainfall_models import RainfallModel, StormScenario
from .tailwater_models import NalaTailwaterEngine
from .risk_scorer import FloodRiskScorer, RiskBreakdown

@dataclass
class TimeStepResult:
    """Simulation results at a specific nowcasting time step."""
    time_minutes: float
    rainfall_intensity_mmhr: float
    # Per-node arrays/dicts (aligned to node_index 0..N-1)
    node_depths_cm: np.ndarray
    node_stress: np.ndarray
    node_surcharge_m3s: np.ndarray
    node_frs: np.ndarray
    node_categories: List[str]
    # Per-edge results
    edge_depths_cm: Dict[Tuple[str, str], float]
    edge_passability: Dict[Tuple[str, str], str]  # 'passable', 'caution', 'impassable'
    edge_categories: Dict[Tuple[str, str], str]
    # Summary statistics
    total_rain_volume_m3: float
    total_runoff_volume_m3: float
    total_discharged_volume_m3: float
    total_surface_excess_volume_m3: float
    overloaded_nodes_count: int
    overloaded_nodes_pct: float
    flooded_edges_count: int
    impassable_road_length_km: float
    max_depth_cm: float
    mean_depth_cm: float
    zone_composite_frs: float
    zone_risk_category: str

class HydraulicCouplingEngine:
    """
    Core hydraulic coupling engine for urban stormwater routing and street flood nowcasting.
    Simulates stormwater routing across the directed acyclic graph network.
    """

    PILOT_PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "spatial", "pilot")
    VALIDATION_PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "spatial", "validation")

    def __init__(
        self,
        zone: str = "pilot",
        data_dir: Optional[str] = None,
        runoff_c: float = 0.85,
        siltation_factor: float = 0.75,
    ):
        self.zone = zone
        self.runoff_c = runoff_c
        self.siltation_factor = siltation_factor

        # Determine data directory
        if data_dir:
            self.data_dir = data_dir
        else:
            self.data_dir = self.VALIDATION_PROCESSED_DIR if zone == "validation" else self.PILOT_PROCESSED_DIR

        # Helper models
        self.rainfall_model = RainfallModel()
        self.tailwater_engine = NalaTailwaterEngine()
        self.risk_scorer = FloodRiskScorer()

        # Internal graph and lookup data
        self.G: Optional[nx.DiGraph] = None
        self.meta: Dict[str, Any] = {}
        self.nodes_list: List[str] = []
        self.node_to_idx: Dict[str, int] = {}
        self.idx_to_node: Dict[int, str] = {}
        self.topo_sorted_nodes: List[str] = []

        # Vectorized node property arrays (aligned to 0..N-1 node_index)
        self.node_x: np.ndarray = np.array([])
        self.node_y: np.ndarray = np.array([])
        self.node_lon: np.ndarray = np.array([])
        self.node_lat: np.ndarray = np.array([])
        self.node_elev: np.ndarray = np.array([])
        self.node_own_area: np.ndarray = np.array([])
        self.node_up_area: np.ndarray = np.array([])
        self.node_ponding_area: np.ndarray = np.array([])
        self.node_capacity: np.ndarray = np.array([])
        self.node_is_outlet: np.ndarray = np.array([], dtype=bool)
        self.node_is_min: np.ndarray = np.array([], dtype=bool)

        # Edge geometries and properties
        self.edges_list: List[Tuple[str, str]] = []
        self.edge_capacities: Dict[Tuple[str, str], float] = {}
        self.edge_lengths: Dict[Tuple[str, str], float] = {}
        self.edge_road_types: Dict[Tuple[str, str], str] = {}

        # Outfall mapping to primary nalas
        self.outfall_nala_mapping: Dict[str, str] = {}

        self.load_network()

    def load_network(self) -> None:
        """Loads and parses the drainage network graph and metadata."""
        graphml_path = os.path.join(self.data_dir, "drainage_graph.graphml")
        meta_path = os.path.join(self.data_dir, "drainage_graph.json")

        if not os.path.exists(graphml_path):
            alt_path = os.path.join(self.PILOT_PROCESSED_DIR, "drainage_graph.graphml")
            if os.path.exists(alt_path):
                graphml_path = alt_path
                meta_path = os.path.join(self.PILOT_PROCESSED_DIR, "drainage_graph.json")
            else:
                raise FileNotFoundError(f"GraphML not found at: {graphml_path}. Run pipelines/network_graph_builder.py first.")

        # Load NetworkX Graph
        self.G = nx.read_graphml(graphml_path)

        with open(meta_path, "r", encoding="utf-8") as f:
            meta_doc = json.load(f)
            self.meta = meta_doc.get("meta", {})

        n_nodes = self.G.number_of_nodes()
        self.nodes_list = sorted(self.G.nodes, key=lambda n: int(self.G.nodes[n]["node_index"]))
        self.node_to_idx = {n: i for i, n in enumerate(self.nodes_list)}
        self.idx_to_node = {i: n for i, n in enumerate(self.nodes_list)}

        # Topological sorting for exact downstream traversal
        self.topo_sorted_nodes = sorted(
            self.nodes_list, key=lambda n: int(self.G.nodes[n].get("topo_order", 0))
        )

        # Allocate arrays
        self.node_x = np.zeros(n_nodes, dtype=float)
        self.node_y = np.zeros(n_nodes, dtype=float)
        self.node_lon = np.zeros(n_nodes, dtype=float)
        self.node_lat = np.zeros(n_nodes, dtype=float)
        self.node_elev = np.zeros(n_nodes, dtype=float)
        self.node_own_area = np.zeros(n_nodes, dtype=float)
        self.node_up_area = np.zeros(n_nodes, dtype=float)
        self.node_ponding_area = np.zeros(n_nodes, dtype=float)
        self.node_capacity = np.zeros(n_nodes, dtype=float)
        self.node_is_outlet = np.zeros(n_nodes, dtype=bool)
        self.node_is_min = np.zeros(n_nodes, dtype=bool)

        outfall_dict = {}
        for i, n in enumerate(self.nodes_list):
            d = self.G.nodes[n]
            self.node_lon[i] = float(d.get("lon", 0.0))
            self.node_lat[i] = float(d.get("lat", 0.0))
            self.node_x[i] = float(d.get("x_utm", d.get("x", 0.0)))
            self.node_y[i] = float(d.get("y_utm", d.get("y", 0.0)))
            self.node_elev[i] = float(d.get("elev_m", d.get("terrain_m", 0.0)))
            self.node_own_area[i] = float(d.get("catchment_area_m2", 0.0))
            min_cell_area = float(self.meta.get("dem_cell_area_m2", 840.0))
            # CRITICAL RULE: Floor ponding area to at least one DEM cell corridor
            self.node_ponding_area[i] = max(
                float(d.get("ponding_area_m2", self.node_own_area[i])),
                min_cell_area
            )
            # Upstream area must also be bounded by at least the local corridor footprint
            self.node_up_area[i] = max(
                float(d.get("upstream_area_m2", self.node_own_area[i])),
                self.node_ponding_area[i]
            )
            self.node_capacity[i] = float(d.get("outgoing_capacity_m3s", 0.0)) * self.siltation_factor
            is_out = bool(int(d.get("is_outlet", 0)) == 1 or d.get("node_type") == "outfall")
            self.node_is_outlet[i] = is_out
            self.node_is_min[i] = bool(int(d.get("is_local_min", 0)) == 1 or d.get("node_type") == "depression")

            if is_out:
                outfall_dict[n] = {
                    "x": self.node_x[i],
                    "y": self.node_y[i],
                    "lon": self.node_lon[i],
                    "lat": self.node_lat[i],
                }

        # Edges
        self.edges_list = list(self.G.edges)
        for u, v in self.edges_list:
            edata = self.G.edges[u, v]
            self.edge_capacities[(u, v)] = float(edata.get("capacity_m3s", 0.5)) * self.siltation_factor
            self.edge_lengths[(u, v)] = float(edata.get("length_m", 50.0))
            self.edge_road_types[(u, v)] = str(edata.get("highway", "residential"))

        # Map outfalls to primary nalas
        if outfall_dict:
            self.outfall_nala_mapping = self.tailwater_engine.map_outfalls_to_nalas(outfall_dict)

    def _resolve_rainfall_field(
        self,
        rainfall_intensity_mmhr: Union[float, np.ndarray],
        n_nodes: int,
    ) -> Tuple[np.ndarray, float]:
        """
        Normalises a scalar or per-node rainfall input into (per-node field, representative scalar).

        A scalar stays scalar for every downstream consumer that needs one number (tailwater
        gate, risk scorer, TimeStepResult) so that existing scalar behaviour is unchanged.
        An array must be aligned to node_index 0..N-1 (e.g. a live radar rainfall field).
        """
        field = np.asarray(rainfall_intensity_mmhr, dtype=float)

        if field.ndim == 0:
            scalar = float(field)
            if not np.isfinite(scalar) or scalar < 0.0:
                raise ValueError(
                    f"rainfall_intensity_mmhr must be finite and non-negative, got {scalar}"
                )
            # The original scalar object is returned untouched so that downstream scalar
            # consumers and serialised output stay byte-identical to pre-radar behaviour.
            return np.full(n_nodes, scalar, dtype=float), rainfall_intensity_mmhr  # type: ignore[return-value]

        if field.ndim != 1 or field.shape[0] != n_nodes:
            raise ValueError(
                f"rainfall field must be a scalar or shape ({n_nodes},), got {field.shape}"
            )
        if not np.all(np.isfinite(field)):
            raise ValueError("rainfall field contains non-finite values")
        if np.any(field < 0.0):
            raise ValueError("rainfall field contains negative intensities")

        # A uniform field IS its own mean, so return the constant exactly rather than an
        # area-weighted average that can land a few ULPs away. This is what makes a uniform
        # field bit-identical to the equivalent scalar call.
        if np.all(field == field[0]):
            return field, float(field[0])

        weights = self.node_own_area
        total_weight = float(weights.sum())
        if total_weight > 0.0:
            representative = float((field * weights).sum() / total_weight)
        else:
            representative = float(field.mean())
        return field, representative

    def simulate_timestep(
        self,
        rainfall_intensity_mmhr: Union[float, np.ndarray],
        dt_seconds: float = 900.0,
        t_minutes: float = 0.0,
        prev_excess_volume: Optional[np.ndarray] = None,
    ) -> Tuple[TimeStepResult, np.ndarray]:
        """
        Runs hydraulic flow routing and ponding calculations for a single time interval.
        Returns (TimeStepResult, current_excess_volume_array).

        `rainfall_intensity_mmhr` accepts either a single zone-wide intensity (scalar, the
        original behaviour) or a per-node field aligned to node_index 0..N-1 (e.g. a decoded
        weather-radar rainfall field). A scalar and a uniform field are numerically identical.
        """
        n_nodes = len(self.nodes_list)
        if prev_excess_volume is None:
            prev_excess_volume = np.zeros(n_nodes, dtype=float)

        intensity_field, representative_mmhr = self._resolve_rainfall_field(
            rainfall_intensity_mmhr, n_nodes
        )

        # 1. Surface Runoff Generation
        # Q_lateral = C * (i / 3.6e6) * A_catchment
        intensity_ms = intensity_field / 3.6e6
        v_rain = intensity_ms * self.node_own_area * dt_seconds
        v_runoff = self.runoff_c * v_rain
        q_lateral = v_runoff / dt_seconds

        # 2. Graph Inflow Accumulation along Topological Order
        q_in = q_lateral.copy()
        q_surcharge = np.zeros(n_nodes, dtype=float)
        q_conduit_out = np.zeros(n_nodes, dtype=float)
        outfall_discharges: Dict[str, float] = {}

        for u in self.topo_sorted_nodes:
            u_idx = self.node_to_idx[u]
            u_qin = q_in[u_idx]

            if self.node_is_outlet[u_idx]:
                # Outfall discharges into receiving nala
                outfall_discharges[u] = u_qin
                q_conduit_out[u_idx] = u_qin
                q_surcharge[u_idx] = 0.0
            else:
                successors = list(self.G.successors(u))
                if successors:
                    v = successors[0]  # Exactly 1 outgoing conduit in drainage tree
                    v_idx = self.node_to_idx[v]
                    cap = self.edge_capacities.get((u, v), self.node_capacity[u_idx])

                    if u_qin <= cap:
                        q_conduit_out[u_idx] = u_qin
                        q_surcharge[u_idx] = 0.0
                    else:
                        q_conduit_out[u_idx] = cap
                        q_surcharge[u_idx] = u_qin - cap

                    # Propagate conduit flow to successor
                    q_in[v_idx] += q_conduit_out[u_idx]
                else:
                    # Isolated leaf/outfall fallback
                    q_conduit_out[u_idx] = u_qin
                    q_surcharge[u_idx] = 0.0

        # 3. Finite Nala Outfall Tailwater Backwater Computation
        # The nala gate is a zone-level CPHEEO threshold, so it consumes the representative
        # (area-weighted) intensity of the field rather than a per-node vector.
        tailwater_depths_by_outfall = self.tailwater_engine.compute_tailwater_depths(
            outfall_discharges, self.outfall_nala_mapping, representative_mmhr
        )
        tailwater_depth_m = np.zeros(n_nodes, dtype=float)
        for outfall_id, tw in tailwater_depths_by_outfall.items():
            tw_idx = self.node_to_idx.get(outfall_id)
            if tw_idx is not None:
                tailwater_depth_m[tw_idx] = tw

        # 4. Surface Storage Volume & Drain-down Accounting
        # If Q_in < Q_cap and previous excess > 0, the node drains down
        curr_excess_volume = np.zeros(n_nodes, dtype=float)
        q_drain_down = np.zeros(n_nodes, dtype=float)

        for i in range(n_nodes):
            cap = self.node_capacity[i]
            inflow = q_in[i]
            prev_v = prev_excess_volume[i]

            if inflow < cap and prev_v > 0.0:
                avail_cap = cap - inflow
                q_dd = min(prev_v / dt_seconds, avail_cap)
                q_drain_down[i] = q_dd
                delta_v = (q_surcharge[i] - q_dd) * dt_seconds
            else:
                delta_v = q_surcharge[i] * dt_seconds

            curr_excess_volume[i] = max(0.0, prev_v + delta_v)

        # 5. Dual-Mechanism Inundation Depth (No numerical traps)
        # Mechanism A: Local terrain depression accumulation
        depth_pond_m = np.divide(
            curr_excess_volume,
            self.node_ponding_area,
            out=np.zeros(n_nodes, dtype=float),
            where=self.node_ponding_area > 0
        )

        # Mechanism B: Upstream conveyance surcharge backup
        depth_backup_m = np.divide(
            curr_excess_volume,
            self.node_up_area,
            out=np.zeros(n_nodes, dtype=float),
            where=self.node_up_area > 0
        )

        # Net node depth in cm
        node_depths_cm = np.zeros(n_nodes, dtype=float)
        for i in range(n_nodes):
            if self.node_is_outlet[i]:
                node_depths_cm[i] = tailwater_depth_m[i] * 100.0
            elif self.node_is_min[i]:
                # True local depression
                node_depths_cm[i] = depth_pond_m[i] * 100.0
            else:
                # Conveyance surcharge over upstream catchment
                node_depths_cm[i] = depth_backup_m[i] * 100.0

        # Cap unrealistic local numerical spikes at physical maximum
        node_depths_cm = np.clip(node_depths_cm, 0.0, 150.0)

        # 6. Drainage Capacity Stress Ratio
        node_stress = np.divide(
            q_in,
            self.node_capacity,
            out=np.zeros(n_nodes, dtype=float),
            where=self.node_capacity > 0
        )

        # 7. Composite Flood Risk Scores (FRS)
        # The FRS rainfall term is a zone-level weighting, so it uses the representative
        # intensity; spatial variation enters through depth and drainage stress.
        node_frs, node_cats, _ = self.risk_scorer.score_batch(
            rainfall_mmhr=representative_mmhr,
            is_local_min=self.node_is_min,
            drainage_stress=node_stress,
            depths_cm=node_depths_cm,
            x_utm=self.node_x,
            y_utm=self.node_y,
            imperviousness=self.runoff_c
        )

        # 8. Street Edge Depth & Vehicle Passability
        edge_depths_cm: Dict[Tuple[str, str], float] = {}
        edge_passability: Dict[Tuple[str, str], str] = {}
        edge_categories: Dict[Tuple[str, str], str] = {}
        impassable_length_km = 0.0
        flooded_edge_count = 0

        for u, v in self.edges_list:
            u_idx = self.node_to_idx[u]
            v_idx = self.node_to_idx[v]
            e_depth = max(float(node_depths_cm[u_idx]), float(node_depths_cm[v_idx]))
            edge_depths_cm[(u, v)] = round(e_depth, 1)

            length_m = self.edge_lengths.get((u, v), 50.0)

            if e_depth > 15.0:
                edge_passability[(u, v)] = "impassable"
                edge_categories[(u, v)] = "HIGH"
                impassable_length_km += length_m / 1000.0
                flooded_edge_count += 1
            elif e_depth >= 5.0:
                edge_passability[(u, v)] = "caution"
                edge_categories[(u, v)] = "MODERATE"
                flooded_edge_count += 1
            else:
                edge_passability[(u, v)] = "passable"
                edge_categories[(u, v)] = "LOW"

        # 9. Mass Balance & Summary
        tot_rain_vol = float(v_rain.sum())
        tot_runoff_vol = float(v_runoff.sum())
        tot_discharged_vol = float(sum(outfall_discharges.values()) * dt_seconds)
        tot_surface_excess_vol = float(curr_excess_volume.sum())

        interior_mask = ~self.node_is_outlet
        overloaded_count = int(((node_stress > 1.0) & interior_mask).sum())
        overloaded_pct = float(100.0 * overloaded_count / max(interior_mask.sum(), 1))

        max_d = float(node_depths_cm.max())
        mean_d = float(node_depths_cm[node_depths_cm > 0].mean()) if (node_depths_cm > 0).any() else 0.0
        zone_frs = float(node_frs.mean())

        if max_d > 15.0 or zone_frs >= 0.70:
            zone_risk = "HIGH"
        elif max_d >= 5.0 or zone_frs >= 0.40:
            zone_risk = "MODERATE"
        else:
            zone_risk = "LOW"

        result = TimeStepResult(
            time_minutes=t_minutes,
            rainfall_intensity_mmhr=representative_mmhr,
            node_depths_cm=node_depths_cm,
            node_stress=node_stress,
            node_surcharge_m3s=q_surcharge,
            node_frs=node_frs,
            node_categories=node_cats,
            edge_depths_cm=edge_depths_cm,
            edge_passability=edge_passability,
            edge_categories=edge_categories,
            total_rain_volume_m3=tot_rain_vol,
            total_runoff_volume_m3=tot_runoff_vol,
            total_discharged_volume_m3=tot_discharged_vol,
            total_surface_excess_volume_m3=tot_surface_excess_vol,
            overloaded_nodes_count=overloaded_count,
            overloaded_nodes_pct=overloaded_pct,
            flooded_edges_count=flooded_edge_count,
            impassable_road_length_km=round(impassable_length_km, 2),
            max_depth_cm=round(max_d, 1),
            mean_depth_cm=round(mean_d, 1),
            zone_composite_frs=round(zone_frs, 3),
            zone_risk_category=zone_risk,
        )

        return result, curr_excess_volume

    def simulate_nowcast(
        self,
        storm_id: str,
        time_steps_min: Optional[List[float]] = None
    ) -> Dict[float, TimeStepResult]:
        """
        Simulates dynamic nowcasting progression across all time steps for a given storm.
        Default time steps: [0, 15, 30, 45, 60, 90, 120, 180] minutes.

        When the gap between consecutive output times exceeds 15 minutes, the engine
        internally sub-steps at 15-minute intervals so that rainfall volume is computed
        correctly for each sub-interval. Only results at the requested output times are
        returned.
        """
        return self._run_series(
            lambda t: self.rainfall_model.get_intensity_at_time(storm_id, t),
            time_steps_min
        )

    def simulate_persistent_field(
        self,
        rainfall_field_mmhr: Union[float, np.ndarray],
        time_steps_min: Optional[List[float]] = None
    ) -> Dict[float, TimeStepResult]:
        """
        Runs the full nowcast horizon with one observed per-node rainfall field held constant
        at every internal sub-step (persistence nowcast).

        This is deliberately NOT storm-cell advection: the newest observed field is assumed to
        persist for the whole horizon. Every result is therefore conditional on the current
        field and carries no lead-time skill claim beyond persistence.
        """
        field, _ = self._resolve_rainfall_field(rainfall_field_mmhr, len(self.nodes_list))
        return self._run_series(lambda t: field, time_steps_min)

    def _run_series(
        self,
        intensity_at,
        time_steps_min: Optional[List[float]] = None
    ) -> Dict[float, TimeStepResult]:
        """
        Shared sub-stepping loop. `intensity_at(t_minutes)` supplies the rainfall input at each
        internal sub-step — a scalar from a scenario hyetograph, or a per-node field.
        """
        if time_steps_min is None:
            time_steps_min = [0.0, 15.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0]

        SUB_DT_MIN = 15.0  # Internal sub-step size in minutes
        SUB_DT_S = SUB_DT_MIN * 60.0  # 900 seconds

        results: Dict[float, TimeStepResult] = {}
        prev_excess = np.zeros(len(self.nodes_list), dtype=float)
        sorted_times = sorted(time_steps_min)

        prev_t = 0.0
        for t_out in sorted_times:
            if t_out == 0.0:
                # First step: single 15-min step at t=0 intensity
                intensity = intensity_at(0.0)
                step_res, prev_excess = self.simulate_timestep(
                    rainfall_intensity_mmhr=intensity,
                    dt_seconds=SUB_DT_S,
                    t_minutes=0.0,
                    prev_excess_volume=prev_excess
                )
                results[0.0] = step_res
                prev_t = 0.0
                continue

            gap_min = t_out - prev_t
            if gap_min <= SUB_DT_MIN:
                # Single sub-step
                intensity = intensity_at(t_out)
                step_res, prev_excess = self.simulate_timestep(
                    rainfall_intensity_mmhr=intensity,
                    dt_seconds=gap_min * 60.0,
                    t_minutes=t_out,
                    prev_excess_volume=prev_excess
                )
                results[t_out] = step_res
            else:
                # Multiple sub-steps within this gap
                n_sub = int(np.ceil(gap_min / SUB_DT_MIN))
                sub_dt_actual = gap_min / n_sub  # Equal sub-intervals
                sub_dt_s = sub_dt_actual * 60.0

                for k in range(n_sub):
                    sub_t = prev_t + (k + 1) * sub_dt_actual
                    intensity = intensity_at(sub_t)
                    step_res, prev_excess = self.simulate_timestep(
                        rainfall_intensity_mmhr=intensity,
                        dt_seconds=sub_dt_s,
                        t_minutes=sub_t,
                        prev_excess_volume=prev_excess
                    )

                # Record the result at the output time (last sub-step)
                results[t_out] = step_res

            prev_t = t_out

        return results

    def export_geojson_layers(
        self,
        step_result: TimeStepResult
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Generates standard GeoJSON feature collections for nodes (Point) and edges (LineString)
        at a specific time step, ready for Leaflet map rendering.
        """
        # Node GeoJSON
        node_features = []
        for i, nid in enumerate(self.nodes_list):
            feat = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(self.node_lon[i], 6), round(self.node_lat[i], 6)],
                },
                "properties": {
                    "node_id": nid,
                    "node_index": i,
                    "depth_cm": round(float(step_result.node_depths_cm[i]), 1),
                    "stress_ratio": round(float(step_result.node_stress[i]), 2),
                    "surcharge_m3s": round(float(step_result.node_surcharge_m3s[i]), 3),
                    "frs_score": round(float(step_result.node_frs[i]), 3),
                    "risk_category": step_result.node_categories[i],
                    "is_outlet": bool(self.node_is_outlet[i]),
                    "is_local_min": bool(self.node_is_min[i]),
                    "terrain_m": round(float(self.node_elev[i]), 2),
                },
            }
            node_features.append(feat)

        nodes_geojson = {
            "type": "FeatureCollection",
            "name": f"drainage_nodes_nowcast_t{int(step_result.time_minutes)}",
            "features": node_features,
        }

        # Edge GeoJSON
        edge_features = []
        for u, v in self.edges_list:
            u_idx = self.node_to_idx[u]
            v_idx = self.node_to_idx[v]
            depth_cm = step_result.edge_depths_cm.get((u, v), 0.0)
            passability = step_result.edge_passability.get((u, v), "passable")
            category = step_result.edge_categories.get((u, v), "LOW")

            feat = {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(self.node_lon[u_idx], 6), round(self.node_lat[u_idx], 6)],
                        [round(self.node_lon[v_idx], 6), round(self.node_lat[v_idx], 6)],
                    ],
                },
                "properties": {
                    "u": u,
                    "v": v,
                    "depth_cm": depth_cm,
                    "passability": passability,
                    "risk_category": category,
                    "highway": self.edge_road_types.get((u, v), "residential"),
                    "length_m": round(self.edge_lengths.get((u, v), 50.0), 1),
                    "capacity_m3s": round(self.edge_capacities.get((u, v), 0.5), 3),
                },
            }
            edge_features.append(feat)

        edges_geojson = {
            "type": "FeatureCollection",
            "name": f"drainage_edges_nowcast_t{int(step_result.time_minutes)}",
            "features": edge_features,
        }

        return nodes_geojson, edges_geojson
