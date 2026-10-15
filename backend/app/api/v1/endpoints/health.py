"""System health, version, asset index counts, and runtime status."""

from datetime import datetime, timezone
from fastapi import APIRouter

from backend.app.config import settings
from backend.app.schemas.common import HealthResponse
from backend.app.services.file_index import file_index
from backend.app.services.geojson_loader import geojson_loader
from backend.app.services.timeline_loader import timeline_loader

router = APIRouter(tags=["System"])


@router.get("/health", response_model=HealthResponse, summary="System Health & Asset Index Status")
async def get_health() -> HealthResponse:
    """Returns server operational status, total indexed GeoJSON map assets, and LRU cache statistics."""
    cache_stats = {
        "geojson_cache": geojson_loader.get_cache_stats(),
        "timeline_cache": timeline_loader.get_cache_stats(),
    }

    return HealthResponse(
        status="ok",
        version=settings.APP_VERSION,
        city="Lucknow, Uttar Pradesh",
        supported_zones=settings.SUPPORTED_ZONES,
        default_zone=settings.DEFAULT_ZONE,
        server_time_iso=datetime.now(timezone.utc).isoformat(),
        total_geojson_assets_indexed=file_index.total_geojson_count,
        scenarios_indexed_count=len(file_index.get_scenarios_for_zone("pilot")),
        cache_stats=cache_stats,
    )
