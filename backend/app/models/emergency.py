"""Community incident reports, crowd verification, emergency shelters, and SOS requests (SS-03)."""

from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.app.core.database import Base


class EmergencyShelter(Base):
    """Designated emergency evacuation shelters and relief camps."""

    __tablename__ = "emergency_shelters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    address = Column(String(255), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    zone = Column(String(50), default="pilot", nullable=False)

    capacity = Column(Integer, default=500, nullable=False)
    current_occupancy = Column(Integer, default=0, nullable=False)
    status = Column(String(20), default="OPEN", nullable=False)  # OPEN, NEAR_CAPACITY, FULL, CLOSED
    amenities = Column(String(255), default="Food, Water, Medical, Electricity", nullable=False)
    contact_person = Column(String(100), nullable=True)
    contact_phone = Column(String(30), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class ReliefResource(Base):
    """Emergency resources and relief distribution points."""

    __tablename__ = "relief_resources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    category = Column(String(50), nullable=False)  # WATER_TANKER, FOOD_DEPOT, MEDICAL_CAMP, RESCUE_BOATS
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    status = Column(String(20), default="AVAILABLE", nullable=False)  # AVAILABLE, DEPLETED, DISPATCHED
    available_units = Column(Integer, default=1, nullable=False)
    contact_phone = Column(String(30), nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class IncidentReport(Base):
    """Crowd-sourced citizen incident reports with verification metrics."""

    __tablename__ = "incident_reports"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    hazard_type = Column(String(50), default="FLOOD", nullable=False)  # FLOOD, FIRE, TREE_FALL, ROAD_DAMAGE, GAS_LEAK, POWER_LINE
    title = Column(String(150), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(20), default="MODERATE", nullable=False)  # LOW, MODERATE, HIGH, CRITICAL

    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    address_description = Column(String(255), nullable=True)
    photo_url = Column(String(500), nullable=True)

    status = Column(String(20), default="PENDING", nullable=False)  # PENDING, VERIFIED, RESOLVED, FALSE_REPORT
    confirmations_count = Column(Integer, default=0, nullable=False)
    disputes_count = Column(Integer, default=0, nullable=False)
    trust_score = Column(Float, default=1.0, nullable=False)

    reported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    user = relationship("User", backref="incident_reports")
    verifications = relationship("IncidentVerification", back_populates="incident", cascade="all, delete-orphan")


class IncidentVerification(Base):
    """Citizen corroboration and verification votes for reported hazards."""

    __tablename__ = "incident_verifications"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incident_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    vote_type = Column(String(20), default="CONFIRM", nullable=False)  # CONFIRM, CLEAR, DISPUTE
    comment = Column(String(255), nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    incident = relationship("IncidentReport", back_populates="verifications")
    user = relationship("User")


class AssistanceRequest(Base):
    """Emergency SOS assistance requests from stranded or vulnerable residents."""

    __tablename__ = "assistance_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    requester_name = Column(String(100), nullable=False)
    contact_phone = Column(String(30), nullable=False)
    request_type = Column(String(50), default="RESCUE", nullable=False)  # RESCUE, MEDICAL, FOOD_WATER, EVACUATION
    urgency_level = Column(String(20), default="HIGH", nullable=False)   # STANDARD, HIGH, CRITICAL

    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    address_details = Column(String(255), nullable=True)
    stranded_count = Column(Integer, default=1, nullable=False)
    vulnerability_flags = Column(String(255), default="", nullable=True)  # ELDERLY, CHILDREN, DISABLED, CRITICAL_MEDS
    notes = Column(Text, nullable=True)

    status = Column(String(20), default="PENDING", nullable=False)  # PENDING, ASSIGNED, IN_PROGRESS, RESOLVED
    assigned_team = Column(String(100), nullable=True)
    coordinator_notes = Column(Text, nullable=True)
    requested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    user = relationship("User", backref="assistance_requests")
