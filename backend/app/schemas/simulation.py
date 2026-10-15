"""Pydantic models for custom rainfall simulations and hydraulic calibrations."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CustomSimulateRequest(BaseModel):
    """Parameters for on-demand hydraulic simulation."""

    rainfall_intensity_mmhr: float = Field(default=60.0, ge=1.0, le=300.0, description="Rainfall intensity in mm/hr")
    duration_hours: float = Field(default=2.0, ge=0.25, le=24.0, description="Storm duration in hours")
    zone: str = Field(default="pilot", description="Drainage zone: 'pilot'")
    runoff_c: float = Field(default=0.85, ge=0.1, le=1.0, description="Runoff coefficient")
    siltation_factor: float = Field(default=0.75, ge=0.1, le=1.0, description="Conduit siltation factor")
    time_steps_min: Optional[List[float]] = Field(default=None, description="Custom evaluation timesteps in minutes")


class CustomSimulateResponse(BaseModel):
    """Output metrics from a completed simulation run."""

    zone: str
    rainfall_intensity_mmhr: float
    duration_hours: float
    simulation_time_s: float
    peak_depth_cm: float
    peak_overloaded_pct: float
    timeline: List[Dict[str, Any]]


class CalibrationMetricsResponse(BaseModel):
    """Validation against historical flood sites and Copernicus DEM."""

    ground_truth_sites_count: int
    proximity_p_value: float
    scientific_confidence_pct: float
    site_ranking_percentile: float
    model_mean_distance_to_known_floods_km: float
    random_chance_distance_km: float
    downhill_flow_conformance_pct: float
    copernicus_vs_srtm_elevation_correlation: float
    drainage_checks_passed: str
