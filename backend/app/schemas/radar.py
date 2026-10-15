"""Pydantic schemas for Doppler radar rainfall feeds and nowcasting."""

from typing import List, Optional
from pydantic import BaseModel, Field


class RadarFrameMeta(BaseModel):
    """Metadata for the decoded radar precipitation grid."""

    source: str
    station: Optional[str] = None
    frame_time_utc: str
    crs: str = "EPSG:4326"
    bounds: List[float]
    grid_shape: List[int]
    cell_size_deg: List[float]
    nodata_cells: int = 0
    field_mean_mmhr: float
    field_max_mmhr: float


class RadarCoverage(BaseModel):
    """Drainage network coverage metrics under the radar footprint."""

    node_count: int
    coverage_fraction: float
    uncovered_node_count: int
    fill_value_mmhr: float
    covered_mean_mmhr: float
    covered_min_mmhr: float
    covered_max_mmhr: float


class RadarStatusResponse(BaseModel):
    """Radar feed ingestion and availability status."""

    enabled: bool
    configured: bool
    available: bool
    station: str
    reason: Optional[str] = None
    detail: Optional[str] = None
    nowcast_mode: str = "persistence"


class RadarTimestep(BaseModel):
    """Single timestep outcome of a radar nowcast."""

    time_minutes: float
    rainfall_intensity_mmhr: float
    max_depth_cm: float
    mean_depth_cm: float
    overloaded_nodes_count: int
    overloaded_nodes_pct: float
    flooded_edges_count: int
    impassable_road_length_km: float
    zone_composite_frs: float
    zone_risk_category: str
    total_rain_volume_m3: float
    total_runoff_volume_m3: float
    total_discharged_volume_m3: float
    total_surface_excess_volume_m3: float


class RadarNowcastResponse(BaseModel):
    """Radar-driven flood nowcast across forecast horizon."""

    zone: str
    nowcast_mode: str = "persistence"
    frame: RadarFrameMeta
    coverage: RadarCoverage
    timeline: List[RadarTimestep]
    peak_depth_cm: float
    peak_time_minutes: float
    simulation_time_s: float
