"""Disaster alert network, evacuation shelters, crowd incident verification, and SOS rescue (SS-03)."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.security import get_current_user, require_roles
from backend.app.models.user import User
from backend.app.schemas.emergency import (
    AssistanceRequestCreate,
    AssistanceRequestResponse,
    AssistanceRequestUpdate,
    EmergencyShelterCreate,
    EmergencyShelterResponse,
    EmergencyShelterUpdate,
    IncidentReportCreate,
    IncidentReportResponse,
    IncidentVerificationCreate,
    ReliefResourceCreate,
    ReliefResourceResponse,
)
from backend.app.services.emergency_service import emergency_service

router = APIRouter(prefix="/emergency", tags=["Emergency Operations (SS-03)"])


# --- Evacuation Shelters ---

@router.get("/shelters", response_model=List[EmergencyShelterResponse], summary="List Evacuation Shelters")
def list_shelters(
    zone: str = Query(default="pilot"),
    shelter_status: Optional[str] = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve designated emergency evacuation shelters with real-time occupancy percentages."""
    return emergency_service.list_shelters(db=db, zone=zone, status=shelter_status)


@router.post("/shelters", response_model=EmergencyShelterResponse, status_code=status.HTTP_201_CREATED, summary="Register Evacuation Shelter")
def create_shelter(
    payload: EmergencyShelterCreate,
    current_user: User = Depends(require_roles(["EMERGENCY", "MUNICIPAL", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Register a designated community emergency shelter."""
    return emergency_service.create_shelter(db=db, payload=payload)


@router.put("/shelters/{shelter_id}", response_model=EmergencyShelterResponse, summary="Update Shelter Occupancy & Status")
def update_shelter(
    shelter_id: int,
    payload: EmergencyShelterUpdate,
    current_user: User = Depends(require_roles(["EMERGENCY", "MUNICIPAL", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Update shelter occupancy, capacity, or operational status in real time."""
    try:
        return emergency_service.update_shelter(db=db, shelter_id=shelter_id, payload=payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/shelters/nearest", response_model=EmergencyShelterResponse, summary="Find Closest Available Shelter")
def find_nearest_shelter(
    lon: float = Query(..., ge=-180.0, le=180.0),
    lat: float = Query(..., ge=-90.0, le=90.0),
    zone: str = Query(default="pilot"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Locate the closest open evacuation shelter with available capacity."""
    result = emergency_service.find_nearest_shelter(db=db, lon=lon, lat=lat, zone=zone)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No open shelters available in this zone.")
    shelter, distance_m = result
    return shelter


# --- Relief Resources ---

@router.get("/resources", response_model=List[ReliefResourceResponse], summary="List Relief Deployments")
def list_resources(
    category: Optional[str] = Query(default=None),
    resource_status: Optional[str] = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Track field emergency resources (water tankers, rescue boats, food depots, medical camps)."""
    return emergency_service.list_resources(db=db, category=category, status=resource_status)


@router.post("/resources", response_model=ReliefResourceResponse, status_code=status.HTTP_201_CREATED, summary="Register Relief Deployment")
def create_resource(
    payload: ReliefResourceCreate,
    current_user: User = Depends(require_roles(["EMERGENCY", "MUNICIPAL", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Deploy emergency relief resource or dispatch equipment."""
    return emergency_service.create_resource(db=db, payload=payload)


# --- Community Incident Reports & Crowd Verification ---

@router.get("/incidents", response_model=List[IncidentReportResponse], summary="List Citizen Hazard Reports")
def list_incidents(
    hazard_type: Optional[str] = Query(default=None),
    incident_status: Optional[str] = Query(default=None, alias="status"),
    min_severity: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List crowd-sourced hazard reports with verification tallies and trust scores."""
    return emergency_service.list_incidents(
        db=db,
        hazard_type=hazard_type,
        status=incident_status,
        min_severity=min_severity,
    )


@router.post("/incidents", response_model=IncidentReportResponse, status_code=status.HTTP_201_CREATED, summary="Report Observed Hazard")
def report_incident(
    payload: IncidentReportCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit a localized flood, obstruction, electrical, or infrastructure hazard."""
    return emergency_service.report_incident(db=db, user_id=current_user.id, payload=payload)


@router.post("/incidents/{incident_id}/verify", response_model=IncidentReportResponse, summary="Corroborate or Dispute Hazard Report")
def verify_incident(
    incident_id: int,
    payload: IncidentVerificationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vote CONFIRM, CLEAR, or DISPUTE on a reported incident with automated consensus."""
    try:
        return emergency_service.verify_incident(
            db=db,
            incident_id=incident_id,
            user_id=current_user.id,
            payload=payload,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# --- SOS Rescue & Assistance ---

@router.post("/sos", response_model=AssistanceRequestResponse, status_code=status.HTTP_201_CREATED, summary="Submit SOS Emergency Rescue Request")
def submit_sos_request(
    payload: AssistanceRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Transmit urgent distress call with GPS coordinates and vulnerability flags (elderly, infants, medical)."""
    return emergency_service.submit_sos(db=db, user_id=current_user.id, payload=payload)


@router.get("/sos", response_model=List[AssistanceRequestResponse], summary="List Active SOS Rescue Requests")
def list_sos_requests(
    sos_status: Optional[str] = Query(default=None, alias="status"),
    urgency: Optional[str] = Query(default=None),
    current_user: User = Depends(require_roles(["EMERGENCY", "MUNICIPAL", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Prioritized emergency distress queue for rescue team dispatch."""
    return emergency_service.list_sos(db=db, status=sos_status, urgency=urgency)


@router.put("/sos/{request_id}", response_model=AssistanceRequestResponse, summary="Update SOS Dispatch & Assigned Team")
def update_sos_request(
    request_id: int,
    payload: AssistanceRequestUpdate,
    current_user: User = Depends(require_roles(["EMERGENCY", "MUNICIPAL", "ADMIN"])),
    db: Session = Depends(get_db),
):
    """Assign field team and update status (ASSIGNED, IN_PROGRESS, RESOLVED)."""
    try:
        return emergency_service.update_sos(db=db, request_id=request_id, payload=payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
