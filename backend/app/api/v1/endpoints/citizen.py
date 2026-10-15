"""Citizen saved locations, alerting preferences, commute monitoring, and notification inbox."""

from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.security import get_current_user
from backend.app.models.citizen import (
    AlertSchedule,
    Notification,
    NotificationPreference,
    SavedLocation,
    TravelSchedule,
)
from backend.app.models.user import User
from backend.app.schemas.citizen import (
    AlertScheduleResponse,
    AlertScheduleUpdate,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    NotificationResponse,
    PaginatedNotifications,
    SavedLocationCreate,
    SavedLocationResponse,
    TravelScheduleCreate,
    TravelScheduleResponse,
)
from backend.app.schemas.common import MessageResponse
from backend.app.services.early_warning_service import early_warning_service

router = APIRouter(prefix="/citizen", tags=["Citizen Operations"])


# --- Saved Locations ---

@router.get("/locations", response_model=List[SavedLocationResponse], summary="List Citizen Saved Locations")
def list_saved_locations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve all monitored places (Home, Work, College) saved by the citizen."""
    return (
        db.query(SavedLocation)
        .filter(SavedLocation.user_id == current_user.id)
        .order_by(SavedLocation.created_at.desc())
        .all()
    )


@router.post("/locations", response_model=SavedLocationResponse, status_code=status.HTTP_201_CREATED, summary="Create Saved Location")
def create_saved_location(
    payload: SavedLocationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register a new monitored location for flood early warning nowcasts."""
    loc = SavedLocation(
        user_id=current_user.id,
        label=payload.label,
        address=payload.address,
        latitude=payload.latitude,
        longitude=payload.longitude,
        preferred_travel_mode=payload.preferred_travel_mode,
        created_at=datetime.now(timezone.utc),
    )
    db.add(loc)
    db.commit()
    db.refresh(loc)
    return loc


@router.delete("/locations/{location_id}", response_model=MessageResponse, summary="Delete Saved Location")
def delete_saved_location(
    location_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a saved location."""
    loc = (
        db.query(SavedLocation)
        .filter(SavedLocation.id == location_id, SavedLocation.user_id == current_user.id)
        .first()
    )
    if not loc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved location not found.")

    db.delete(loc)
    db.commit()
    return MessageResponse(message="Location deleted successfully.")


# --- Notification Preferences ---

@router.get("/preferences", response_model=NotificationPreferenceResponse, summary="Get Notification Preferences")
def get_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return citizen alert thresholds and delivery channels."""
    pref = (
        db.query(NotificationPreference)
        .filter(NotificationPreference.user_id == current_user.id)
        .first()
    )
    if not pref:
        pref = NotificationPreference(
            user_id=current_user.id,
            enable_rain_alerts=True,
            enable_flood_alerts=True,
            enable_drainage_alerts=True,
            enable_emergency_broadcasts=True,
            min_severity_threshold="MODERATE",
            channel_dashboard=True,
            channel_browser_push=True,
            channel_sms=False,
        )
        db.add(pref)
        db.commit()
        db.refresh(pref)
    return pref


@router.put("/preferences", response_model=NotificationPreferenceResponse, summary="Update Notification Preferences")
def update_preferences(
    payload: NotificationPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update alerting criteria and channels."""
    pref = (
        db.query(NotificationPreference)
        .filter(NotificationPreference.user_id == current_user.id)
        .first()
    )
    if not pref:
        pref = NotificationPreference(user_id=current_user.id)
        db.add(pref)

    if payload.enable_rain_alerts is not None:
        pref.enable_rain_alerts = payload.enable_rain_alerts
    if payload.enable_flood_alerts is not None:
        pref.enable_flood_alerts = payload.enable_flood_alerts
    if payload.enable_drainage_alerts is not None:
        pref.enable_drainage_alerts = payload.enable_drainage_alerts
    if payload.enable_emergency_broadcasts is not None:
        pref.enable_emergency_broadcasts = payload.enable_emergency_broadcasts
    if payload.min_severity_threshold is not None:
        pref.min_severity_threshold = payload.min_severity_threshold
    if payload.channel_dashboard is not None:
        pref.channel_dashboard = payload.channel_dashboard
    if payload.channel_browser_push is not None:
        pref.channel_browser_push = payload.channel_browser_push
    if payload.channel_sms is not None:
        pref.channel_sms = payload.channel_sms

    db.commit()
    db.refresh(pref)
    return pref


# --- Commute Travel Schedules ---

@router.get("/travel-schedules", response_model=List[TravelScheduleResponse], summary="List Commute Routes")
def list_travel_schedules(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List recurring commute schedules monitored for route flooding."""
    schedules = (
        db.query(TravelSchedule)
        .filter(TravelSchedule.user_id == current_user.id)
        .order_by(TravelSchedule.created_at.desc())
        .all()
    )
    results = []
    for s in schedules:
        results.append(
            TravelScheduleResponse(
                id=s.id,
                user_id=s.user_id,
                name=s.name,
                origin_location_id=s.origin_location_id,
                destination_location_id=s.destination_location_id,
                origin_label=s.origin.label if s.origin else None,
                destination_label=s.destination.label if s.destination else None,
                outbound_time=s.outbound_time,
                return_time=s.return_time,
                days_of_week=s.days_of_week,
                preferred_travel_mode=s.preferred_travel_mode,
                is_enabled=s.is_enabled,
                created_at=s.created_at,
            )
        )
    return results


@router.post("/travel-schedules", response_model=TravelScheduleResponse, status_code=status.HTTP_201_CREATED, summary="Create Commute Route")
def create_travel_schedule(
    payload: TravelScheduleCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register daily commute route between saved locations."""
    orig = db.query(SavedLocation).filter(SavedLocation.id == payload.origin_location_id, SavedLocation.user_id == current_user.id).first()
    dest = db.query(SavedLocation).filter(SavedLocation.id == payload.destination_location_id, SavedLocation.user_id == current_user.id).first()
    if not orig or not dest:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid origin or destination saved location.")

    schedule = TravelSchedule(
        user_id=current_user.id,
        name=payload.name,
        origin_location_id=payload.origin_location_id,
        destination_location_id=payload.destination_location_id,
        outbound_time=payload.outbound_time,
        return_time=payload.return_time,
        days_of_week=payload.days_of_week,
        preferred_travel_mode=payload.preferred_travel_mode,
        is_enabled=payload.is_enabled,
        created_at=datetime.now(timezone.utc),
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    return TravelScheduleResponse(
        id=schedule.id,
        user_id=schedule.user_id,
        name=schedule.name,
        origin_location_id=schedule.origin_location_id,
        destination_location_id=schedule.destination_location_id,
        origin_label=orig.label,
        destination_label=dest.label,
        outbound_time=schedule.outbound_time,
        return_time=schedule.return_time,
        days_of_week=schedule.days_of_week,
        preferred_travel_mode=schedule.preferred_travel_mode,
        is_enabled=schedule.is_enabled,
        created_at=schedule.created_at,
    )


@router.delete("/travel-schedules/{schedule_id}", response_model=MessageResponse, summary="Delete Commute Route")
def delete_travel_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a commute route."""
    sched = db.query(TravelSchedule).filter(TravelSchedule.id == schedule_id, TravelSchedule.user_id == current_user.id).first()
    if not sched:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Commute schedule not found.")
    db.delete(sched)
    db.commit()
    return MessageResponse(message="Schedule deleted successfully.")


# --- Notifications Inbox ---

@router.get("/notifications", response_model=PaginatedNotifications, summary="Get Citizen Notification Inbox")
def get_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
):
    """Retrieve dispatched early warnings, broadcasts, and hazard advisories."""
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread_only:
        query = query.filter(Notification.is_read == False)

    total_count = query.count()
    unread_count = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.is_read == False)
        .count()
    )

    items = query.order_by(Notification.alert_sent_time.desc()).limit(limit).all()

    formatted_items = [
        NotificationResponse(
            id=n.id,
            user_id=n.user_id,
            saved_location_id=n.saved_location_id,
            location_label=n.saved_location.label if n.saved_location else n.location_name,
            title=n.title,
            message=n.message,
            hazard_type=n.hazard_type,
            severity=n.severity,
            expected_depth_cm=n.expected_depth_cm,
            alert_sent_time=n.alert_sent_time,
            status=n.status,
            is_read=n.is_read,
        )
        for n in items
    ]

    return PaginatedNotifications(
        total_count=total_count,
        unread_count=unread_count,
        items=formatted_items,
    )


@router.post("/notifications/{notification_id}/read", response_model=MessageResponse, summary="Mark Notification Read")
def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark advisory notification as read."""
    n = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == current_user.id).first()
    if not n:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    n.is_read = True
    db.commit()
    return MessageResponse(message="Notification marked as read.")


@router.post("/notifications/evaluate", response_model=List[NotificationResponse], summary="Trigger Nowcast Alert Evaluation")
def evaluate_nowcast_alerts(
    zone: str = "pilot",
    scenario: str = "CPHEEO-EXTREME-60MM",
    timestep_minutes: Optional[float] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Evaluate current nowcast against saved locations and generate 30-min lead time alerts."""
    alerts = early_warning_service.evaluate_citizen_alerts(
        db=db,
        zone=zone,
        scenario=scenario,
        timestep_minutes=timestep_minutes,
    )
    return [
        NotificationResponse(
            id=a.id,
            user_id=a.user_id,
            saved_location_id=a.saved_location_id,
            location_label=a.location_name,
            title=a.title,
            message=a.message,
            hazard_type=a.hazard_type,
            severity=a.severity,
            expected_depth_cm=a.expected_depth_cm,
            alert_sent_time=a.alert_sent_time,
            status=a.status,
            is_read=a.is_read,
        )
        for a in alerts
        if a.user_id == current_user.id
    ]
