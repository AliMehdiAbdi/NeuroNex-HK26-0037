"""Outfall discharge and tailwater backflow models for receiving drainage nalas."""

from dataclasses import dataclass
import json
import os
import re
from typing import Dict, List, Optional, Tuple
import numpy as np
from pyproj import Transformer


@dataclass
class PrimaryNala:
    """Represents a primary municipal drainage channel/nala."""

    id: str
    name: str
    bank: str
    catchment: str
    outfall_location_desc: str
    lon: float
    lat: float
    receiving_water: str
    approx_length_km: float
    design_capacity_m3s: float
    type: str


class NalaTailwaterEngine:
    """Simulates tailwater backwater stage at outfall discharge points."""

    DEFAULT_NALAS_PATH = os.path.join("Drainage", "data", "lucknow_primary_nalas.json")
    UTM_CRS = "EPSG:32644"

    def __init__(self, nalas_json_path: Optional[str] = None):
        path = nalas_json_path or self.DEFAULT_NALAS_PATH
        self.nalas: Dict[str, PrimaryNala] = {}
        self.to_utm = Transformer.from_crs("EPSG:4326", self.UTM_CRS, always_xy=True)
        self.load_nalas(path)

    def load_nalas(self, json_path: str) -> None:
        """Loads primary nala definitions from JSON catalog."""
        if not os.path.exists(json_path):
            alt_path = os.path.join(os.path.dirname(__file__), "..", json_path)
            if os.path.exists(alt_path):
                json_path = alt_path
            else:
                raise FileNotFoundError(f"Nalas catalog not found at: {json_path}")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for item in data.get("nalas", []):
            loc_str = item.get("outfall_location", "")
            coords = self._extract_coords(loc_str)
            if not coords:
                coords = (80.930, 26.850)

            nala = PrimaryNala(
                id=item["id"],
                name=item["name"],
                bank=item.get("bank", "Cis-Gomti"),
                catchment=item.get("catchment", ""),
                outfall_location_desc=loc_str,
                lon=coords[0],
                lat=coords[1],
                receiving_water=item.get("receiving_water", "Gomti River"),
                approx_length_km=float(item.get("approx_length_km", 5.0)),
                design_capacity_m3s=float(item.get("design_capacity_m3s", 15.0)),
                type=item.get("type", "Open Masonry Nala"),
            )
            self.nalas[nala.id] = nala

    def _extract_coords(self, text: str) -> Optional[Tuple[float, float]]:
        """Parses (lat, lon) coordinates from descriptive strings."""
        match = re.search(r"\(?(\d+\.\d+)N,\s*(\d+\.\d+)E\)?", text)
        if match:
            lat = float(match.group(1))
            lon = float(match.group(2))
            return (lon, lat)
        return None

    def map_outfalls_to_nalas(
        self, outfall_nodes: Dict[str, Dict[str, float]]
    ) -> Dict[str, str]:
        """Maps each outfall node ID to its closest receiving nala."""
        nala_ids = list(self.nalas.keys())
        nala_xy = np.array([
            self.to_utm.transform(self.nalas[nid].lon, self.nalas[nid].lat)
            for nid in nala_ids
        ])

        outfall_mapping = {}
        for nid, data in outfall_nodes.items():
            if "x" in data and "y" in data:
                ox, oy = data["x"], data["y"]
            else:
                ox, oy = self.to_utm.transform(data["lon"], data["lat"])

            dists = np.hypot(nala_xy[:, 0] - ox, nala_xy[:, 1] - oy)
            best_idx = int(np.argmin(dists))
            outfall_mapping[nid] = nala_ids[best_idx]

        return outfall_mapping

    def compute_tailwater_depths(
        self,
        outfall_discharges_m3s: Dict[str, float],
        outfall_mapping: Dict[str, str],
        external_storm_intensity_mmhr: float = 0.0,
    ) -> Dict[str, float]:
        """Computes backwater depth (m) when channel capacity is exceeded."""
        # Aggregate discharge per receiving nala
        nala_inflow: Dict[str, float] = {nid: 0.0 for nid in self.nalas}
        for outfall_id, q in outfall_discharges_m3s.items():
            nala_id = outfall_mapping.get(outfall_id)
            if nala_id and nala_id in nala_inflow:
                nala_inflow[nala_id] += max(0.0, q)

        tailwater_by_outfall: Dict[str, float] = {}
        for outfall_id, nala_id in outfall_mapping.items():
            nala = self.nalas.get(nala_id)
            if not nala:
                tailwater_by_outfall[outfall_id] = 0.0
                continue

            q_in = nala_inflow[nala_id]
            q_cap = nala.design_capacity_m3s
            stress = q_in / max(q_cap, 1e-4)

            # Standard municipal design baseline is 15 mm/hr
            if external_storm_intensity_mmhr > 15.0:
                excess_ratio = (external_storm_intensity_mmhr - 15.0) / 45.0
                # Clamp backwater stage between 0 and 0.45m
                tw_depth = float(np.clip(excess_ratio * 0.20 * min(max(stress, 0.5), 2.0), 0.0, 0.45))
            else:
                tw_depth = 0.0

            tailwater_by_outfall[outfall_id] = tw_depth

        return tailwater_by_outfall
