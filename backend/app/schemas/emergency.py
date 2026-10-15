"""Pydantic schemas for emergency disaster management, crowd verification, and SOS rescue (SS-03)."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

HazardType = Literal["FLOOD", "FIRE", "TREE_FALL", "ROAD_DAMAGE", "GAS_LEAK", "POWER_LINE", "WATERLOGGING"]
SeverityLevel = Literal["LOW", "MODERATE", "HIGH", "CRITICAL"]
ShelterStatus = Literal["OPEN", "NEAR_CAPACITY", "FULL", "CLOSED"]
ResourceCategory = Literal["WATER_TANKER", "FOOD_DEPOT", "MEDICAL_CAMP", "RESCUE_BOATS", "EVACUATION_BUS"]
VoteType = Literal["CONFIRM", "CLEAR", "DISPUTE"]
SOSRequestType = Literal["RESCUE", "MEDICAL", "FOOD_WATER", "EVACUATION"]
SOSUrgency = Literal["STANDARD", "HIGH", "CRITICAL"]
SOSStatus = Literal["PENDING", "ASSIGNED", "IN_PROGRESS", "RESOLVED"]


# --- Emergency Shelters ---

class EmergencyShelterCreate(BaseModel):
    """Register a designated evacuation shelter."""

    name: str = Field(min_length=2, max_length=150)
    address: str = Field(min_length=3, max_length=255)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    zone: str = "pilot"
    capacity: int = Field(default=500, gt=0)
    amenities: str = "Food, Water, Medical, Electricity"
    contact_person: Optional[str] = None
    contact_phone: str = Field(min_length=5, max_length=30)


class EmergencyShelterUpdate(BaseModel):
    """Update shelter status and occupancy in real time."""

    capacity: Optional[int] = Field(default=None, gt=0)
    current_occupancy: Optional[int] = Field(default=None, ge=0)
    status: Optional[ShelterStatus] = None
    amenities: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None


class EmergencyShelterResponse(BaseModel):
    """Evacuation shelter details with real-time occupancy."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    address: str
    latitude: float
    longitude: float
    zone: str
    capacity: int
    current_occupancy: int
    occupancy_pct: float = 0.0
    status: str
    amenities: str
    contact_person: Optional[str] = None
    contact_phone: str
    created_at: datetime


# --- Relief Resources ---

class ReliefResourceCreate(BaseModel):
    """Register deployed relief resources."""

    name: str = Field(min_length=2, max_length=150)
    category: ResourceCategory
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    status: str = "AVAILABLE"
    available_units: int = Field(default=1, ge=0)
    contact_phone: Optional[str] = None


class ReliefResourceResponse(BaseModel):
    """Relief resource deployment details."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    latitude: float
    longitude: float
    status: str
    available_units: int
    contact_phone: Optional[str] = None
    updated_at: datetime


# --- Crowd Incident Reports & Verification ---

class IncidentVerificationCreate(BaseModel):
    """Corroboration vote on a citizen hazard report."""

    vote_type: VoteType = "CONFIRM"
    comment: Optional[str] = Field(default=None, max_length=255)


class IncidentVerificationResponse(BaseModel):
    """Logged verification vote."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    user_id: int
    vote_type: str
    comment: Optional[str] = None
    timestamp: datetime


class IncidentReportCreate(BaseModel):
    """Citizen report for observed floods or hazards."""

    hazard_type: HazardType = "FLOOD"
    title: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=5)
    severity: SeverityLevel = "MODERATE"
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    address_description: Optional[str] = Field(default=None, max_length=255)
    photo_url: Optional[str] = Field(default=None, max_length=500)


class IncidentReportResponse(BaseModel):
    """Incident report with crowd verification metrics."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    hazard_type: str
    title: str
    description: str
    severity: str
    latitude: float
    longitude: float
    address_description: Optional[str] = None
    photo_url: Optional[str] = None
    status: str
    confirmations_count: int
    disputes_count: int
    trust_score: float
    reported_at: datetime
    resolved_at: Optional[datetime] = None
    verifications: List[IncidentVerificationResponse] = []


class IncidentStatusUpdate(BaseModel):
    """Municipal verification and status override."""

    status: Literal["PENDING", "VERIFIED", "RESOLVED", "FALSE_REPORT"]


# --- SOS Assistance Requests ---

class AssistanceRequestCreate(BaseModel):
    """Citizen SOS request for rescue or emergency supplies."""

    requester_name: str = Field(min_length=2, max_length=100)
    contact_phone: str = Field(min_length=5, max_length=30)
    request_type: SOSRequestType = "RESCUE"
    urgency_level: SOSUrgency = "HIGH"
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    address_details: Optional[str] = Field(default=None, max_length=255)
    stranded_count: int = Field(default=1, ge=1)
    vulnerability_flags: Optional[str] = Field(default="", description="Comma-separated: ELDERLY, CHILDREN, DISABLED, CRITICAL_MEDS")
    notes: Optional[str] = None


class AssistanceRequestUpdate(BaseModel):
    """Coordinator dispatch and assignment update."""

    status: Optional[SOSStatus] = None
    assigned_team: Optional[str] = Field(default=None, max_length=100)
    coordinator_notes: Optional[str] = None


class AssistanceRequestResponse(BaseModel):
    """Active SOS assistance request record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    requester_name: str
    contact_phone: str
    request_type: str
    urgency_level: str
    latitude: float
    longitude: float
    address_details: Optional[str] = None
    stranded_count: int
    vulnerability_flags: Optional[str] = None
    notes: Optional[str] = None
    status: str
    assigned_team: Optional[str] = None
    coordinator_notes: Optional[str] = None
    requested_at: datetime
    resolved_at: Optional[datetime] = None
