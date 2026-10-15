"""Pydantic schemas for saved locations, alert preferences, commutes, and citizen notifications."""

from datetime import datetime, time
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
TravelMode = Literal["pedestrian", "two_wheeler", "car", "emergency_suv", "ambulance", "bus"]


class SavedLocationCreate(BaseModel):
    """Payload to create a new monitored location."""

    label: str = Field(min_length=1, max_length=100, description="Location label: Home, Office, School, etc.")
    address: Optional[str] = Field(default=None, max_length=255)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    preferred_travel_mode: TravelMode = "car"


class SavedLocationResponse(BaseModel):
    """Serialized citizen saved location."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    label: str
    address: Optional[str] = None
    latitude: float
    longitude: float
    preferred_travel_mode: str
    created_at: datetime


class NotificationPreferenceUpdate(BaseModel):
    """Update alerting preferences and delivery channels."""

    enable_rain_alerts: Optional[bool] = None
    enable_flood_alerts: Optional[bool] = None
    enable_drainage_alerts: Optional[bool] = None
    enable_emergency_broadcasts: Optional[bool] = None
    min_severity_threshold: Optional[str] = Field(default="MODERATE", description="LOW, MODERATE, HIGH, CRITICAL")
    channel_dashboard: Optional[bool] = None
    channel_browser_push: Optional[bool] = None
    channel_sms: Optional[bool] = None


class NotificationPreferenceResponse(BaseModel):
    """Citizen alert subscriptions and channels."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    enable_rain_alerts: bool
    enable_flood_alerts: bool
    enable_drainage_alerts: bool
    enable_emergency_broadcasts: bool
    min_severity_threshold: str
    channel_dashboard: bool
    channel_browser_push: bool
    channel_sms: bool


class AlertScheduleUpdate(BaseModel):
    """Configured active window for receiving non-critical flood alerts."""

    days_of_week: str = "MON,TUE,WED,THU,FRI,SAT,SUN"
    active_from: str = Field(default="06:00", description="HH:MM format")
    active_to: str = Field(default="22:00", description="HH:MM format")
    is_enabled: bool = True

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, value: str) -> str:
        selected = [d.strip() for d in value.split(",")]
        if not selected or any(day not in DAYS for day in selected):
            raise ValueError("Select valid comma-separated days: MON,TUE,WED,THU,FRI,SAT,SUN")
        return ",".join(selected)


class AlertScheduleResponse(BaseModel):
    """Quiet hours and active days for notification delivery."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    days_of_week: str
    active_from: time
    active_to: time
    is_enabled: bool


class TravelScheduleCreate(BaseModel):
    """Daily commute route registered for flood hazard nowcasts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    origin_location_id: int = Field(gt=0)
    destination_location_id: int = Field(gt=0)
    outbound_time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    return_time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    days_of_week: str = "MON,TUE,WED,THU,FRI"
    preferred_travel_mode: TravelMode = "car"
    is_enabled: bool = True

    @field_validator("days_of_week")
    @classmethod
    def validate_days(cls, value: str) -> str:
        selected = [d.strip() for d in value.split(",")]
        if not selected or any(day not in DAYS for day in selected):
            raise ValueError("Select one or more valid days: MON through SUN")
        return ",".join(selected)

    @model_validator(mode="after")
    def validate_journey(self):
        if self.origin_location_id == self.destination_location_id:
            raise ValueError("Origin and destination must be different saved locations")
        if self.return_time <= self.outbound_time:
            raise ValueError("Return departure must be after outbound departure")
        return self


class TravelScheduleResponse(BaseModel):
    """Active commute schedule and associated location labels."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    origin_location_id: int
    destination_location_id: int
    origin_label: Optional[str] = None
    destination_label: Optional[str] = None
    outbound_time: str
    return_time: str
    days_of_week: str
    preferred_travel_mode: str
    is_enabled: bool
    created_at: datetime


class NotificationResponse(BaseModel):
    """Dispatched hazard advisory record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    saved_location_id: Optional[int] = None
    location_label: Optional[str] = None
    title: str
    message: str
    hazard_type: str
    severity: str
    expected_depth_cm: float
    alert_sent_time: datetime
    status: str
    is_read: bool


class PaginatedNotifications(BaseModel):
    """Paginated list of alerts for the citizen dashboard."""

    total_count: int
    unread_count: int
    items: List[NotificationResponse]
