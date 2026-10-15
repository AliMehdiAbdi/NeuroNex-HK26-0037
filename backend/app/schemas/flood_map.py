"""Pydantic schemas for flood map GeoJSON layers and scenario metadata."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class MapMetadata(BaseModel):
    """Hydraulic and geospatial summary for the rendered timestep."""

    zone: str
    scenario_id: str
    scenario_name: str
    timestep_minutes: float
    rainfall_intensity_mmhr: float
    max_depth_cm: float
    mean_depth_cm: float
    overloaded_nodes_pct: float
    impassable_road_length_km: float
    zone_composite_frs: float
    zone_risk_category: str


class FloodMapResponse(BaseModel):
    """GeoJSON FeatureCollections for drainage nodes and road edges."""

    metadata: MapMetadata
    nodes: Dict[str, Any]
    edges: Dict[str, Any]
