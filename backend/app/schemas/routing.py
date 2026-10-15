"""Vehicle-aware flood evacuation and routing schemas."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RouteRequestSchema(BaseModel):
    """Routing request supporting coordinates or node IDs."""

    from_lat: Optional[float] = None
    from_lon: Optional[float] = None
    to_lat: Optional[float] = None
    to_lon: Optional[float] = None
    from_node: Optional[str] = None
    to_node: Optional[str] = None
    zone: str = "pilot"
    mode: str = "all"  # "fastest", "safest", "balanced", or "all"
    scenario: str = "CPHEEO-EXTREME-60MM"
    t: float = 15.0
    vehicle_type: str = "car"  # "car", "two_wheeler", "emergency_suv", "pedestrian", "ambulance"
    custom_clearance_cm: Optional[float] = None


class RouteSegmentSchema(BaseModel):
    """Single street segment along a calculated path."""

    u: str
    v: str
    road_name: str
    highway_class: str
    length_m: float
    flood_depth_cm: float
    passability: str  # "passable", "caution", "impassable"
    travel_time_s: float


class SingleRouteSchema(BaseModel):
    """Route candidate evaluated against flood depth and travel time."""

    mode: str
    mode_label: str
    is_passable: bool
    status: str  # "SAFE", "CAUTION", "IMPASSABLE"
    recommendation: str  # "Recommended", "Caution", "Avoid"
    total_distance_km: float
    total_travel_time_minutes: float
    max_flood_depth_cm: float
    mean_flood_depth_cm: float
    flooded_segments_count: int
    impassable_segments_count: int
    geojson: Dict[str, Any]
    segments: List[RouteSegmentSchema]


class RouteResponseSchema(BaseModel):
    """Complete multi-mode routing response."""

    zone: str
    scenario_id: str
    timestep_minutes: float
    vehicle_type: str
    ground_clearance_cm: float
    start_node: str
    end_node: str
    start_coordinates: List[float]
    end_coordinates: List[float]
    selected_mode: str
    routes: Dict[str, SingleRouteSchema]
    comparison: List[Dict[str, Any]]


class VehiclePresetsResponse(BaseModel):
    """Available vehicle clearance standards and profiles."""

    vehicle_clearances_cm: Dict[str, float]
    default_vehicle: str
    mode_descriptions: Dict[str, str]
