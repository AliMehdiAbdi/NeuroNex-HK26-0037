"""Municipal operations, drainage bottlenecks, hotspot dispatch, and emergency broadcasts."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.security import get_current_user, require_roles
from backend.app.models.user import User
from backend.app.schemas.municipal import (
    BroadcastAlertRequest,
    BroadcastAlertResponse,
    CriticalInfrastructureResponse,
    DrainageProblemResponse,
    HotspotActionRequest,
    HotspotActionResponse,
    HotspotDetailResponse,
    MunicipalNowcastResponse,
)
from backend.app.services.municipal_service import municipal_service

router = APIRouter(prefix="/municipal", tags=["Municipal Operations"])


@router.get("/nowcast", response_model=MunicipalNowcastResponse, summary="Get 0-3h Hydraulic Nowcast Projections")
def get_nowcast_projections(
    zone: str = Query(default="pilot", description="Drainage zone: 'pilot' or 'validation'"),
    scenario: str = Query(default="CPHEEO-EXTREME-60MM", description="Storm scenario identifier"),
    current_user: User = Depends(get_current_user),
):
    """Retrieve 0-3 hour hydraulic nowcast across standard timesteps (NOW, +30m, +1h, +2h, +3h)."""
    return municipal_service.get_0_to_3hr_nowcast(zone=zone, scenario=scenario)


@router.get("/drainage-problems", response_model=DrainageProblemResponse, summary="Get Drainage Network Bottlenecks")
def get_drainage_problems(
    zone: str = Query(default="pilot"),
    scenario: str = Query(default="CPHEEO-EXTREME-60MM"),
    t: float = Query(default=45.0, description="Evaluation timestep in minutes"),
    current_user: User = Depends(get_current_user),
):
    """Identify conduit surcharges, capacity stress ratios, and topographic sinks."""
    return municipal_service.get_drainage_problems(zone=zone, scenario=scenario, t=t)


@router.get("/hotspots", response_model=List[HotspotDetailResponse], summary="Get Priority Flood Hotspots")
def get_priority_hotspots(
    zone: str = Query(default="pilot"),
    scenario: str = Query(default="CPHEEO-EXTREME-60MM"),
    t: float = Query(default=45.0),
    min_risk: str = Query(default="ALL", description="Filter by risk level: ALL, LOW, MODERATE, HIGH, CRITICAL"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ranked waterlogging hotspots with FRS score, street names, and action logs."""
    return municipal_service.get_priority_hotspots(
        db=db,
        zone=zone,
        scenario=scenario,
        t=t,
        min_risk=min_risk,
    )


@router.post("/hotspots/{hotspot_id}/actions", response_model=HotspotActionResponse, summary="Log Mitigation Action on Hotspot")
def log_hotspot_action(
    hotspot_id: int,
    payload: HotspotActionRequest,
    current_user: User = Depends(require_roles(["MUNICIPAL", "EMERGENCY", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Record operator dispatch (e.g. pump deployed, drain cleared, traffic diverted)."""
    try:
        return municipal_service.log_hotspot_action(
            db=db,
            hotspot_id=hotspot_id,
            user_id=current_user.id,
            action_taken=payload.action_taken,
            notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/infrastructure", response_model=CriticalInfrastructureResponse, summary="Get Critical Infrastructure Proximity Exposure")
def get_critical_infrastructure(
    zone: str = Query(default="pilot"),
    scenario: str = Query(default="CPHEEO-EXTREME-60MM"),
    t: float = Query(default=45.0),
    current_user: User = Depends(get_current_user),
):
    """Proximity and predicted flood depth near hospitals, schools, railway, and metro stations."""
    return municipal_service.get_critical_infrastructure_exposure(zone=zone, scenario=scenario, t=t)


@router.post("/broadcast", response_model=BroadcastAlertResponse, summary="Dispatch Emergency Broadcast to Citizens")
def dispatch_broadcast(
    payload: BroadcastAlertRequest,
    current_user: User = Depends(require_roles(["MUNICIPAL", "EMERGENCY", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Broadcast high-priority disaster warning to all registered citizens."""
    return municipal_service.broadcast_alert(
        db=db,
        title=payload.title,
        message=payload.message,
        zone=payload.zone,
        severity=payload.severity,
        target_channel=payload.target_channel,
    )
