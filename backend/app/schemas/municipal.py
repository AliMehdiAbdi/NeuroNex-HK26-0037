"""Pydantic schemas for municipal flood monitoring, actions, infrastructure, and broadcasts."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class HotspotActionSummary(BaseModel):
    """Compact summary of an action taken on a hotspot."""

    id: int
    user_name: Optional[str] = None
    action_taken: str
    notes: Optional[str] = None
    action_timestamp: datetime


class HotspotDetailResponse(BaseModel):
    """Detailed flood risk hotspot with geospatial attributes and recent actions."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    zone: str
    node_id: Optional[str] = None
    locality_name: str
    road_name: Optional[str] = None
    street_names: Optional[str] = None
    latitude: float
    longitude: float

    frs_score: float
    risk_category: str
    predicted_depth_cm: float
    drainage_stress_ratio: float
    rainfall_intensity_mmhr: float
    confidence_pct: float

    notice_status: str
    primary_contributing_factors: Optional[str] = None
    suggested_response_category: Optional[str] = None
    detected_at: datetime
    recent_actions: List[HotspotActionSummary] = []


class HotspotActionRequest(BaseModel):
    """Operator dispatch or mitigation logging payload."""

    action_taken: str = Field(description="Action code: PUMP_DEPLOYED, DRAIN_CLEARED, BARRICADE_ERECTED, DIVERSION_ACTIVE, CREW_DISPATCHED")
    notes: Optional[str] = None


class HotspotActionResponse(BaseModel):
    """Logged mitigation action record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    hotspot_id: int
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    action_taken: str
    notes: Optional[str] = None
    action_timestamp: datetime


class NowcastHorizonItem(BaseModel):
    """Projected flood conditions across a specific forecast horizon."""

    horizon_label: str
    timestep_minutes: float
    rainfall_intensity_mmhr: float
    max_depth_cm: float
    mean_depth_cm: float
    overloaded_nodes_pct: float
    impassable_road_length_km: float
    zone_composite_frs: float
    zone_risk_category: str
    confidence_pct: float


class MunicipalNowcastResponse(BaseModel):
    """Zone-wide hydraulic nowcast across future timesteps."""

    zone: str
    scenario_id: str
    scenario_name: str
    current_time_iso: str
    timeline: List[NowcastHorizonItem]


class DrainageBottleneckItem(BaseModel):
    """Drainage network choke point with surcharge metrics."""

    node_id: str
    locality: str
    elevation_m: float
    drainage_stress_ratio: float
    surcharge_m3s: float
    pipe_capacity_m3s: float
    bottleneck_cause: str


class DrainageProblemResponse(BaseModel):
    """Drainage network stress summary."""

    zone: str
    timestep_minutes: float
    total_manholes: int
    surcharged_manholes_count: int
    surcharged_percentage: float
    total_conduits: int
    conduits_over_capacity_count: int
    bottlenecks: List[DrainageBottleneckItem]


class CriticalInfrastructureItem(BaseModel):
    """Monitored municipal asset and its proximity to flood risk."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    latitude: float
    longitude: float
    zone: str
    baseline_elevation_m: float
    distance_to_nearest_hotspot_m: Optional[float] = None
    predicted_flood_depth_cm: Optional[float] = 0.0
    exposure_level: Optional[str] = "LOW"


class CriticalInfrastructureResponse(BaseModel):
    """List of monitored infrastructure assets."""

    total_count: int
    critical_count: int
    items: List[CriticalInfrastructureItem]


class BroadcastAlertRequest(BaseModel):
    """Emergency broadcast dispatch payload."""

    title: str = Field(min_length=3, max_length=150)
    message: str = Field(min_length=5)
    zone: str = "pilot"
    severity: str = Field(default="HIGH", description="LOW, MODERATE, HIGH, CRITICAL")
    target_channel: str = Field(default="ALL", description="DASHBOARD, PUSH, SMS, ALL")


class BroadcastAlertResponse(BaseModel):
    """Broadcast execution confirmation."""

    status: str
    broadcast_id: str
    recipients_count: int
    sent_at: datetime
