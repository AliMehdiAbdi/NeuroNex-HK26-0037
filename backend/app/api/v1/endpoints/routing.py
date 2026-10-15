"""Safe evacuation and flood-aware vehicle routing endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.config import settings
from backend.app.core.exceptions import RoutingException
from backend.app.core.security import get_current_user
from backend.app.models.user import User
from backend.app.schemas.routing import (
    RouteRequestSchema,
    RouteResponseSchema,
    VehiclePresetsResponse,
)
from backend.app.services.routing_service import routing_service

router = APIRouter(prefix="/routing", tags=["Evacuation Routing"])


@router.post("/route", response_model=RouteResponseSchema, summary="Calculate Safe Navigation Routes")
def calculate_route(
    payload: RouteRequestSchema,
    current_user: User = Depends(get_current_user),
):
    """Compute Dijkstra routes with vehicle clearance barriers across Fastest, Safest, and Balanced modes."""
    try:
        return routing_service.compute_safe_routes(payload)
    except RoutingException as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Routing calculation failed: {str(e)}",
        )


@router.get("/presets", response_model=VehiclePresetsResponse, summary="Get Vehicle Profiles and Clearance Standards")
def get_vehicle_presets():
    """Return standard vehicle ground clearances (cm) and mode navigation descriptions."""
    return VehiclePresetsResponse(
        vehicle_clearances_cm=settings.VEHICLE_CLEARANCES_CM,
        default_vehicle="car",
        mode_descriptions={
            "fastest": "Optimizes for shortest travel time along major arterial roads, accepting shallow surface ponding.",
            "safest": "Avoids flood risk zones entirely, prioritizing elevated ridge corridors and dry paths.",
            "balanced": "Optimal compromise between travel delay and flood water depth.",
        },
    )
