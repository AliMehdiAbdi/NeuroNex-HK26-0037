"""Flood map GeoJSON layer delivery and scenario catalog endpoints."""

from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.core.exceptions import AssetNotFoundException, ScenarioNotFoundException, ZoneNotFoundException
from backend.app.core.security import get_current_user
from backend.app.models.user import User
from backend.app.schemas.flood_map import FloodMapResponse, MapMetadata
from backend.app.services.file_index import file_index
from backend.app.services.geojson_loader import geojson_loader
from backend.app.services.timeline_loader import timeline_loader

router = APIRouter(prefix="/map", tags=["Flood Mapping"])


@router.get("/layers", response_model=FloodMapResponse, summary="Get Map GeoJSON FeatureCollections")
def get_map_layers(
    zone: str = Query(default="pilot"),
    scenario: str = Query(default="CPHEEO-EXTREME-60MM"),
    t: float = Query(default=15.0, description="Timestep in minutes"),
    current_user: User = Depends(get_current_user),
):
    """Retrieve GeoJSON nodes and road edges with hydraulic depth properties for visualization."""
    try:
        z = file_index.validate_zone(zone)
        slug = file_index.resolve_slug(scenario)

        nodes_fc = geojson_loader.load_layer(z, slug, t, "nodes")
        edges_fc = geojson_loader.load_layer(z, slug, t, "edges")
        timeline_doc = timeline_loader.load_timeline(z, slug)

        steps = timeline_doc.get("timeline", [])
        matched = min(steps, key=lambda s: abs(s["time_minutes"] - t))

        meta = MapMetadata(
            zone=z,
            scenario_id=slug.upper(),
            scenario_name=timeline_doc.get("scenario", {}).get("name", slug.replace("_", " ").title()),
            timestep_minutes=t,
            rainfall_intensity_mmhr=float(matched.get("rainfall_intensity_mmhr", 0.0)),
            max_depth_cm=float(matched.get("max_depth_cm", 0.0)),
            mean_depth_cm=float(matched.get("mean_depth_cm", 0.0)),
            overloaded_nodes_pct=float(matched.get("overloaded_nodes_pct", 0.0)),
            impassable_road_length_km=float(matched.get("impassable_road_length_km", 0.0)),
            zone_composite_frs=float(matched.get("zone_composite_frs", 0.0)),
            zone_risk_category=str(matched.get("zone_risk_category", "LOW")),
        )

        return FloodMapResponse(
            metadata=meta,
            nodes=nodes_fc,
            edges=edges_fc,
        )
    except (AssetNotFoundException, ScenarioNotFoundException, ZoneNotFoundException) as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/scenarios", response_model=List[Dict[str, Any]], summary="List Available Scenarios for Zone")
def list_scenarios(
    zone: str = Query(default="pilot"),
    current_user: User = Depends(get_current_user),
):
    """Return available pre-computed rainfall events and peak hydraulic statistics."""
    try:
        return file_index.get_scenarios_for_zone(zone=zone)
    except ZoneNotFoundException as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
