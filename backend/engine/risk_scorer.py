"""Multi-factor composite flood risk score (FRS) calculation engine."""

from dataclasses import dataclass
import json
import os
from typing import Dict, List, Optional, Tuple, Union
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree


@dataclass
class RiskBreakdown:
    """Multi-variable breakdown of composite flood risk."""

    score: float
    category: str
    color: str
    r_factor: float
    t_factor: float
    d_factor: float
    i_factor: float
    h_factor: float
    depth_cm: float
    advisory: str


class FloodRiskScorer:
    """Calculates composite Flood Risk Scores (FRS) for nodes and road corridors."""

    DEFAULT_SITES_PATH = os.path.join(
        os.path.dirname(__file__), "..", "data", "spatial", "pilot", "flood_sites.json"
    )
    UTM_CRS = "EPSG:32644"

    DEFAULT_WEIGHTS = {
        "w_rain": 0.35,
        "w_terrain": 0.20,
        "w_drainage": 0.25,
        "w_impervious": 0.10,
        "w_historical": 0.10,
    }

    def __init__(
        self,
        flood_sites_json_path: Optional[str] = None,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.weights = weights or dict(self.DEFAULT_WEIGHTS)
        self.to_utm = Transformer.from_crs("EPSG:4326", self.UTM_CRS, always_xy=True)
        self.known_sites_tree: Optional[cKDTree] = None
        self.known_sites_xy: Optional[np.ndarray] = None
        self._load_flood_sites(flood_sites_json_path or self.DEFAULT_SITES_PATH)

    def _load_flood_sites(self, path: str) -> None:
        """Loads historical flood locations for proximity scoring."""
        if not os.path.exists(path):
            alt_path = os.path.join("backend", "data", "spatial", "pilot", "flood_sites.json")
            if os.path.exists(alt_path):
                path = alt_path
            else:
                return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        coords = []
        for s in data.get("sites", []):
            if s.get("is_strong", True):
                ux, uy = self.to_utm.transform(float(s["lon"]), float(s["lat"]))
                coords.append([ux, uy])

        if coords:
            self.known_sites_xy = np.array(coords)
            self.known_sites_tree = cKDTree(self.known_sites_xy)

    def compute_historical_factor(self, x_utm: np.ndarray, y_utm: np.ndarray) -> np.ndarray:
        """Computes exponential distance decay factor (H) from historical flood hotspots."""
        if self.known_sites_tree is None:
            return np.zeros(len(x_utm), dtype=float)

        points = np.column_stack([x_utm, y_utm])
        dists, _ = self.known_sites_tree.query(points, k=1)
        # Half-distance decay at 400m
        h_scores = np.exp(-dists / 400.0)
        return np.clip(h_scores, 0.0, 1.0)

    def score_node(
        self,
        rainfall_mmhr: float,
        is_local_min: bool,
        drainage_stress: float,
        depth_cm: float,
        x_utm: float,
        y_utm: float,
        imperviousness: float = 0.85,
    ) -> RiskBreakdown:
        """Computes composite risk metrics for an individual drainage junction."""
        r = float(np.clip(rainfall_mmhr / 100.0, 0.0, 1.0))
        t = 1.0 if is_local_min else 0.15
        d = float(np.clip(drainage_stress / 3.0, 0.0, 1.0))
        i = float(np.clip(imperviousness, 0.0, 1.0))

        if self.known_sites_tree is not None:
            dist, _ = self.known_sites_tree.query([x_utm, y_utm], k=1)
            h = float(np.clip(np.exp(-dist / 400.0), 0.0, 1.0))
        else:
            h = 0.0

        frs = (
            self.weights["w_rain"] * r
            + self.weights["w_terrain"] * t
            + self.weights["w_drainage"] * d
            + self.weights["w_impervious"] * i
            + self.weights["w_historical"] * h
        )

        if depth_cm > 15.0 or frs >= 0.70:
            category = "HIGH"
            color = "#EF4444"
            advisory = "Severe waterlogging risk. Impassable for low-clearance vehicles. Avoid corridor."
        elif depth_cm >= 5.0 or frs >= 0.40:
            category = "MODERATE"
            color = "#F59E0B"
            advisory = "Moderate surface ponding expected. Proceed with caution."
        else:
            category = "LOW"
            color = "#10B981"
            advisory = "Normal drainage conditions. Road fully passable."

        return RiskBreakdown(
            score=round(frs, 3),
            category=category,
            color=color,
            r_factor=round(r, 2),
            t_factor=round(t, 2),
            d_factor=round(d, 2),
            i_factor=round(i, 2),
            h_factor=round(h, 2),
            depth_cm=round(depth_cm, 1),
            advisory=advisory,
        )

    def score_batch(
        self,
        rainfall_mmhr: float,
        is_local_min: np.ndarray,
        drainage_stress: np.ndarray,
        depths_cm: np.ndarray,
        x_utm: np.ndarray,
        y_utm: np.ndarray,
        imperviousness: Union[float, np.ndarray] = 0.85,
    ) -> Tuple[np.ndarray, List[str], List[str]]:
        """Vectorized FRS scoring for network arrays."""
        n = len(is_local_min)
        r = float(np.clip(rainfall_mmhr / 100.0, 0.0, 1.0))
        t = np.where(is_local_min, 1.0, 0.15)
        d = np.clip(drainage_stress / 3.0, 0.0, 1.0)
        i = imperviousness if isinstance(imperviousness, np.ndarray) else np.full(n, imperviousness)
        h = self.compute_historical_factor(x_utm, y_utm)

        frs = (
            self.weights["w_rain"] * r
            + self.weights["w_terrain"] * t
            + self.weights["w_drainage"] * d
            + self.weights["w_impervious"] * i
            + self.weights["w_historical"] * h
        )

        categories = []
        colors = []
        for score, depth in zip(frs, depths_cm):
            if depth > 15.0 or score >= 0.70:
                categories.append("HIGH")
                colors.append("#EF4444")
            elif depth >= 5.0 or score >= 0.40:
                categories.append("MODERATE")
                colors.append("#F59E0B")
            else:
                categories.append("LOW")
                colors.append("#10B981")

        return frs, categories, colors
