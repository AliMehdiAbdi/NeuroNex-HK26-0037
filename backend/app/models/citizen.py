"""Citizen preferences, saved places, travel schedules, and alert history."""

from datetime import datetime, timezone, time
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import relationship

from backend.app.core.database import Base


class SavedLocation(Base):
    """Citizen saved locations for personalized hazard monitoring (e.g., Home, Work)."""

    __tablename__ = "saved_locations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String(100), nullable=False)
    address = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    preferred_travel_mode = Column(String(50), default="car", nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", backref="saved_locations")
    notifications = relationship("Notification", back_populates="saved_location")


class NotificationPreference(Base):
    """Citizen alerting criteria and channel subscriptions."""

    __tablename__ = "notification_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    enable_rain_alerts = Column(Boolean, default=True, nullable=False)
    enable_flood_alerts = Column(Boolean, default=True, nullable=False)
    enable_drainage_alerts = Column(Boolean, default=True, nullable=False)
    enable_emergency_broadcasts = Column(Boolean, default=True, nullable=False)
    min_severity_threshold = Column(String(20), default="MODERATE", nullable=False)

    # Notification channels
    channel_dashboard = Column(Boolean, default=True, nullable=False)
    channel_browser_push = Column(Boolean, default=True, nullable=False)
    channel_sms = Column(Boolean, default=False, nullable=False)

    user = relationship("User", backref="notification_preference", uselist=False)


class AlertSchedule(Base):
    """Quiet hours and active days for notification delivery."""

    __tablename__ = "alert_schedules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    days_of_week = Column(String(50), default="MON,TUE,WED,THU,FRI,SAT,SUN", nullable=False)
    active_from = Column(Time, default=time(6, 0), nullable=False)
    active_to = Column(Time, default=time(22, 0), nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)

    user = relationship("User", backref="alert_schedules")


class TravelSchedule(Base):
    """Recurring commute routes monitored for real-time flood hazards."""

    __tablename__ = "travel_schedules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    origin_location_id = Column(Integer, ForeignKey("saved_locations.id", ondelete="CASCADE"), nullable=False)
    destination_location_id = Column(Integer, ForeignKey("saved_locations.id", ondelete="CASCADE"), nullable=False)
    outbound_time = Column(String(5), nullable=False)
    return_time = Column(String(5), nullable=False)
    days_of_week = Column(String(50), default="MON,TUE,WED,THU,FRI", nullable=False)
    preferred_travel_mode = Column(String(50), default="car", nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", backref="travel_schedules")
    origin = relationship("SavedLocation", foreign_keys=[origin_location_id])
    destination = relationship("SavedLocation", foreign_keys=[destination_location_id])


class Notification(Base):
    """Dispatched hazard warnings and alerts record."""

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    saved_location_id = Column(Integer, ForeignKey("saved_locations.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    hazard_type = Column(String(50), default="FLOOD_RISK", nullable=False)
    severity = Column(String(20), default="MODERATE", nullable=False)
    expected_depth_cm = Column(Float, default=0.0, nullable=False)
    alert_sent_time = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    status = Column(String(20), default="SENT", nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)

    user = relationship("User", backref="notifications")
    saved_location = relationship("SavedLocation", back_populates="notifications")
