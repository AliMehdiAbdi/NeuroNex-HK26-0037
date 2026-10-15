"""Municipal operations, risk hotspots, critical infrastructure, and audit records."""

from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.app.core.database import Base


class Hotspot(Base):
    """Identified waterlogging and flood hazard areas."""

    __tablename__ = "hotspots"

    id = Column(Integer, primary_key=True, index=True)
    zone = Column(String(50), default="pilot", nullable=False)
    node_id = Column(String(50), nullable=True, index=True)
    locality_name = Column(String(150), nullable=False)
    road_name = Column(String(150), nullable=True)
    street_names = Column(String(500), nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    frs_score = Column(Float, nullable=False)
    risk_category = Column(String(20), default="HIGH", nullable=False)
    predicted_depth_cm = Column(Float, default=0.0, nullable=False)
    drainage_stress_ratio = Column(Float, default=0.0, nullable=False)
    rainfall_intensity_mmhr = Column(Float, default=0.0, nullable=False)
    confidence_pct = Column(Float, default=85.0, nullable=False)

    notice_status = Column(String(20), default="ACTIVE", nullable=False)
    primary_contributing_factors = Column(Text, nullable=True)
    suggested_response_category = Column(String(255), nullable=True)
    detected_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    actions = relationship("HotspotAction", back_populates="hotspot", cascade="all, delete-orphan")


class HotspotAction(Base):
    """Mitigation and dispatch actions logged by municipal coordinators."""

    __tablename__ = "hotspot_actions"

    id = Column(Integer, primary_key=True, index=True)
    hotspot_id = Column(Integer, ForeignKey("hotspots.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action_taken = Column(String(100), nullable=False)
    notes = Column(Text, nullable=True)
    action_timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    hotspot = relationship("Hotspot", back_populates="actions")
    user = relationship("User")


class CriticalInfrastructure(Base):
    """High-priority municipal assets (hospitals, schools, sub-stations)."""

    __tablename__ = "critical_infrastructure"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    category = Column(String(50), nullable=False)  # HOSPITAL, SCHOOL, METRO, RAILWAY, EMERGENCY, POWER
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    zone = Column(String(50), default="pilot", nullable=False)
    baseline_elevation_m = Column(Float, default=110.0, nullable=False)


class AuditLog(Base):
    """Operator action logs for municipal compliance and audit trail."""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(100), nullable=False)
    target_entity = Column(String(100), nullable=True)
    ip_address = Column(String(50), nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
