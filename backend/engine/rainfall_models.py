"""Storm scenario definitions and temporal rainfall intensity models."""

from dataclasses import dataclass, field
import json
import os
from typing import Dict, List, Optional, Union
import numpy as np


@dataclass
class StormScenario:
    """Represents a rainfall event hyetograph profile."""

    id: str
    name: str
    category: str
    total_depth_mm: float
    duration_hours: float
    peak_intensity_mmhr: float
    news_reference: str
    time_series_15min_mmhr: List[float] = field(default_factory=list)
    description: str = ""

    @property
    def time_step_minutes(self) -> float:
        return 15.0

    @property
    def duration_minutes(self) -> float:
        return self.duration_hours * 60.0


class RainfallModel:
    """Manages storm scenarios and produces interpolated rainfall rates."""

    DEFAULT_STORMS_PATH = os.path.join(
        os.path.dirname(__file__), "..", "data", "catalogs", "historical_storms.json"
    )

    def __init__(self, storms_json_path: Optional[str] = None):
        path = storms_json_path or self.DEFAULT_STORMS_PATH
        self.scenarios: Dict[str, StormScenario] = {}
        self.load_scenarios(path)

    def load_scenarios(self, json_path: str) -> None:
        """Loads storm scenarios from catalog JSON."""
        if not os.path.exists(json_path):
            alt_path = os.path.join("backend", "data", "catalogs", "historical_storms.json")
            if os.path.exists(alt_path):
                json_path = alt_path
            else:
                raise FileNotFoundError(f"Storms catalog not found at: {json_path}")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for s in data.get("scenarios", []):
            scenario = StormScenario(
                id=s["id"],
                name=s["name"],
                category=s.get("category", "General"),
                total_depth_mm=float(s.get("total_depth_mm", 0.0)),
                duration_hours=float(s.get("duration_hours", 2.0)),
                peak_intensity_mmhr=float(s.get("peak_intensity_mmhr", 0.0)),
                news_reference=s.get("news_reference", ""),
                time_series_15min_mmhr=[float(x) for x in s.get("time_series_15min_mmhr", [])],
                description=s.get("description", ""),
            )
            self.scenarios[scenario.id] = scenario

    def list_scenarios(self) -> List[Dict[str, Union[str, float]]]:
        """Returns summary list of registered scenarios."""
        return [
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "peak_intensity_mmhr": s.peak_intensity_mmhr,
                "total_depth_mm": s.total_depth_mm,
                "duration_hours": s.duration_hours,
                "description": s.description,
            }
            for s in self.scenarios.values()
        ]

    def get_scenario(self, storm_id: str) -> StormScenario:
        """Retrieves a scenario by identifier or raises KeyError."""
        if storm_id not in self.scenarios:
            for sid, sc in self.scenarios.items():
                if sid.lower() == storm_id.lower() or storm_id.lower() in sc.name.lower():
                    return sc
            raise KeyError(f"Scenario '{storm_id}' not found. Available: {list(self.scenarios.keys())}")
        return self.scenarios[storm_id]

    def get_intensity_at_time(self, storm_id: str, t_minutes: float) -> float:
        """Linearly interpolates rainfall intensity (mm/hr) at minute t."""
        scenario = self.get_scenario(storm_id)
        series = scenario.time_series_15min_mmhr
        dt = scenario.time_step_minutes

        if t_minutes <= 0:
            return series[0] if series else 0.0

        max_t = len(series) * dt
        if t_minutes >= max_t:
            return 0.0

        idx_float = t_minutes / dt
        idx_low = int(np.floor(idx_float))
        idx_high = min(idx_low + 1, len(series) - 1)
        weight = idx_float - idx_low

        return float((1.0 - weight) * series[idx_low] + weight * series[idx_high])

    def generate_hyetograph(self, storm_id: str, time_steps_min: List[float]) -> List[float]:
        """Generates rainfall intensities for designated time steps."""
        return [self.get_intensity_at_time(storm_id, t) for t in time_steps_min]

    def create_uniform_scenario(
        self,
        intensity_mmhr: float,
        duration_hours: float = 2.0,
        name: Optional[str] = None,
        scenario_id: Optional[str] = None,
    ) -> StormScenario:
        """Generates a constant-intensity benchmark scenario."""
        sid = scenario_id or f"UNIFORM-{int(intensity_mmhr)}MM"
        sname = name or f"Steady {intensity_mmhr:.0f} mm/hr Uniform Storm"
        n_steps = int(np.ceil(duration_hours * 60 / 15.0))
        series = [float(intensity_mmhr)] * n_steps
        total_depth = float(intensity_mmhr * duration_hours)

        scenario = StormScenario(
            id=sid,
            name=sname,
            category="Synthetic Uniform",
            total_depth_mm=total_depth,
            duration_hours=duration_hours,
            peak_intensity_mmhr=float(intensity_mmhr),
            news_reference="Synthetic uniform benchmark scenario",
            time_series_15min_mmhr=series,
            description=f"Constant {intensity_mmhr} mm/hr rainfall over {duration_hours} hours.",
        )
        self.scenarios[scenario.id] = scenario
        return scenario
