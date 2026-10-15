"""Evaluates citizen saved locations and commutes against real-time flood nowcasts."""

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from scipy.spatial import cKDTree
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.citizen import (
    Notification,
    NotificationPreference,
    SavedLocation,
)
from backend.app.models.user import User
from backend.app.services.geojson_loader import geojson_loader
from backend.app.services.timeline_loader import timeline_loader


def as_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensures datetime instance is timezone-aware UTC for arithmetic."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class EarlyWarningService:
    """Evaluates spatial-temporal flood risk against user saved locations."""

    SEVERITY_RANKS = {"LOW": 1, "MODERATE": 2, "HIGH": 3, "CRITICAL": 4}
    HORIZON_LABELS = {0.0: "NOW", 30.0: "+30 MIN", 60.0: "+1 HOUR", 120.0: "+2 HOURS", 180.0: "+3 HOURS"}

    def __init__(self):
        self._spatial_cache: Dict[str, Any] = {}

    @classmethod
    def horizon_label(cls, timestep_min: float) -> str:
        """Human-readable label for a nowcast timestep."""
        if timestep_min in cls.HORIZON_LABELS:
            return cls.HORIZON_LABELS[timestep_min]
        return f"+{int(round(timestep_min))} MIN"

    @staticmethod
    def travel_advice(mode: str) -> str:
        """Vehicle or pedestrian advisory based on travel mode."""
        return {
            "pedestrian": "walk via elevated footpaths and stay clear of subways and open drains",
            "two_wheeler": "expect wheel-deep water; prefer elevated roads and avoid underpasses",
            "car": "use elevated routes and never enter water deeper than 15 cm",
            "emergency_suv": "most ponding is passable, but avoid underpasses with active inflow",
            "ambulance": "most ponding is passable; confirm the approach road to your destination",
            "bus": "expect minor ponding to be passable, but allow extra travel time",
        }.get(mode, "check the safest available route before you travel")

    @staticmethod
    def build_message(
        *,
        loc_label: str,
        address: Optional[str],
        event_time_str: str,
        lead_minutes: float,
        depth_cm: float,
        severity: str,
        horizon_label: str,
        rainfall_mmhr: float,
        stress_ratio: float,
        travel_mode: str,
        change_note: Optional[str] = None,
    ) -> str:
        """Build structured advisory message body."""
        lines = [
            f"Waterlogging is predicted near your saved location \"{loc_label}\" "
            f"around {event_time_str} (about {int(round(lead_minutes))} min from now).",
        ]
        if change_note:
            lines.append(f"Update: {change_note}")
        lines.extend(
            [
                "",
                f"Rainfall intensity : {rainfall_mmhr:.1f} mm/hr  ({horizon_label})",
                f"Predicted depth    : {depth_cm:.1f} cm  ({severity})",
                f"Drainage stress    : {stress_ratio:.2f}x design capacity"
                if stress_ratio > 0
                else "Drainage stress    : within design capacity",
            ]
        )
        if address:
            lines.append(f"Place              : {address}")
        lines.extend(["", f"Travel advice: {EarlyWarningService.travel_advice(travel_mode)}."])
        return "\n".join(lines)

    def evaluate_citizen_alerts(
        self,
        db: Session,
        zone: str = "pilot",
        scenario: str = "CPHEEO-EXTREME-60MM",
        simulated_base_time: Optional[datetime] = None,
        timestep_minutes: Optional[float] = None,
    ) -> List[Notification]:
        """Scan nowcast timesteps, match against user locations, and dispatch early warnings."""
        now = simulated_base_time or datetime.now(timezone.utc)
        timeline_doc = timeline_loader.load_timeline(zone, scenario)
        timeline_steps = timeline_doc.get("timeline", [])
        timesteps = [float(s["time_minutes"]) for s in timeline_steps]
        rainfall_by_timestep: Dict[float, float] = {
            float(s["time_minutes"]): float(s.get("rainfall_intensity_mmhr", 0.0) or 0.0)
            for s in timeline_steps
        }

        requested_timestep: Optional[float] = None
        if timestep_minutes is not None and timesteps:
            requested_timestep = min(timesteps, key=lambda candidate: abs(candidate - float(timestep_minutes)))

        cache_key = f"{zone}_{scenario}"
        if cache_key in self._spatial_cache:
            nids, kdtree, node_coords, node_time_depths, node_time_stress = self._spatial_cache[cache_key]
        else:
            node_time_depths: Dict[str, Dict[float, float]] = {}
            node_time_stress: Dict[str, Dict[float, float]] = {}
            node_coords: Dict[str, Tuple[float, float]] = {}

            for t in timesteps:
                try:
                    fc = geojson_loader.load_layer(zone, scenario, t, "nodes")
                    for feat in fc.get("features", []):
                        props = feat.get("properties", {})
                        nid = str(props.get("node_id", ""))
                        coords = feat.get("geometry", {}).get("coordinates", [])
                        if coords and len(coords) >= 2:
                            node_coords[nid] = (float(coords[0]), float(coords[1]))
                        depth = float(props.get("depth_cm", 0.0))
                        node_time_depths.setdefault(nid, {})[t] = depth
                        node_time_stress.setdefault(nid, {})[t] = float(props.get("stress_ratio", 0.0) or 0.0)
                except Exception:
                    continue

            if not node_coords:
                return []

            nids = list(node_coords.keys())
            coords_arr = np.array([node_coords[nid] for nid in nids])
            kdtree = cKDTree(coords_arr)
            self._spatial_cache[cache_key] = (nids, kdtree, node_coords, node_time_depths, node_time_stress)

        users = db.query(User).filter(User.is_active == True).all()
        generated_or_updated_alerts: List[Notification] = []

        for user in users:
            prefs = user.notification_preference or NotificationPreference(
                user_id=user.id,
                min_severity_threshold="MODERATE",
                enable_flood_alerts=True,
                channel_dashboard=True,
            )

            if not prefs.enable_flood_alerts:
                continue

            min_rank = self.SEVERITY_RANKS.get(prefs.min_severity_threshold.upper(), 2)

            for loc in user.saved_locations:
                dist_deg = 500.0 / 111000.0
                indices = kdtree.query_ball_point([loc.longitude, loc.latitude], r=dist_deg)
                if not indices:
                    continue

                max_depth = 0.0
                peak_stress = 0.0
                peak_timestep_min = requested_timestep if requested_timestep is not None else 0.0

                for idx in indices:
                    nid = nids[idx]
                    t_depths = node_time_depths.get(nid, {})
                    if requested_timestep is not None:
                        depth_at_horizon = t_depths.get(requested_timestep, 0.0)
                        if depth_at_horizon > max_depth:
                            max_depth = depth_at_horizon
                            peak_stress = node_time_stress.get(nid, {}).get(requested_timestep, 0.0)
                    else:
                        for t_min, d in t_depths.items():
                            if d > max_depth:
                                max_depth = d
                                peak_timestep_min = t_min
                                peak_stress = node_time_stress.get(nid, {}).get(t_min, 0.0)

                if max_depth >= 30.0:
                    sev = "CRITICAL"
                elif max_depth >= 15.0:
                    sev = "HIGH"
                elif max_depth >= 5.0:
                    sev = "MODERATE"
                else:
                    sev = "LOW"

                sev_rank = self.SEVERITY_RANKS.get(sev, 1)
                if sev_rank < min_rank:
                    continue

                impact_time = now + timedelta(minutes=peak_timestep_min)
                approx_lead_time_min = peak_timestep_min
                horizon_label = self.horizon_label(peak_timestep_min)
                rainfall_now = rainfall_by_timestep.get(peak_timestep_min, 0.0)

                dedup_raw = f"{user.id}_{loc.id}_{now.strftime('%Y-%m-%d')}"
                dedup_key = hashlib.sha256(dedup_raw.encode("utf-8")).hexdigest()[:24]

                existing = db.query(Notification).filter(Notification.deduplication_key == dedup_key).first()
                event_time_str = impact_time.strftime("%I:%M %p")

                if existing:
                    prev_impact = as_utc_aware(existing.predicted_event_time)
                    prev_depth = float(existing.expected_depth_cm or 0.0)
                    prev_rain = float(existing.rainfall_intensity_mmhr or 0.0)
                    prev_horizon = existing.timestep_minutes

                    horizon_changed = prev_horizon is None or abs(prev_horizon - peak_timestep_min) >= 1.0
                    rainfall_changed = abs(prev_rain - rainfall_now) >= 2.0
                    depth_changed = abs(prev_depth - max_depth) >= 1.0
                    time_shifted = bool(prev_impact) and abs((impact_time - prev_impact).total_seconds()) >= 900
                    severity_changed = existing.severity != sev

                    if horizon_changed or rainfall_changed or depth_changed or time_shifted or severity_changed:
                        notes: List[str] = []
                        if horizon_changed:
                            notes.append(f"now showing the {horizon_label} horizon")
                        if rainfall_changed:
                            notes.append(f"rainfall revised to {rainfall_now:.1f} mm/hr")
                        if depth_changed:
                            notes.append(f"predicted depth revised to {max_depth:.1f} cm")
                        if time_shifted and prev_impact:
                            notes.append(f"onset moved to {event_time_str}")
                        if severity_changed:
                            notes.append(f"severity revised to {sev}")

                        existing.status = "UPDATED"
                        existing.severity = sev
                        existing.expected_depth_cm = round(max_depth, 1)
                        existing.predicted_event_time = impact_time
                        existing.rainfall_intensity_mmhr = round(rainfall_now, 1)
                        existing.timestep_minutes = peak_timestep_min
                        existing.horizon_label = horizon_label
                        existing.drainage_stress_ratio = round(peak_stress, 2)
                        existing.location_name = loc.label
                        existing.hazard_type = "WATERLOGGING" if max_depth < 20 else "FLOOD_RISK"
                        existing.title = f"Alert Updated: Flood risk at {loc.label}"
                        existing.message = self.build_message(
                            loc_label=loc.label,
                            address=loc.address,
                            event_time_str=event_time_str,
                            lead_minutes=approx_lead_time_min,
                            depth_cm=max_depth,
                            severity=sev,
                            horizon_label=horizon_label,
                            rainfall_mmhr=rainfall_now,
                            stress_ratio=peak_stress,
                            travel_mode=loc.preferred_travel_mode,
                            change_note="; ".join(notes) if notes else None,
                        )
                        existing.event_category = "PREDICTION"
                        existing.is_read = False
                        existing.alert_sent_time = now
                        db.commit()
                        generated_or_updated_alerts.append(existing)
                else:
                    new_alert = Notification(
                        user_id=user.id,
                        saved_location_id=loc.id,
                        title=f"30-Minute Early Warning: Flood risk at {loc.label}",
                        message=self.build_message(
                            loc_label=loc.label,
                            address=loc.address,
                            event_time_str=event_time_str,
                            lead_minutes=approx_lead_time_min,
                            depth_cm=max_depth,
                            severity=sev,
                            horizon_label=horizon_label,
                            rainfall_mmhr=rainfall_now,
                            stress_ratio=peak_stress,
                            travel_mode=loc.preferred_travel_mode,
                        ),
                        event_category="PREDICTION",
                        hazard_type="WATERLOGGING" if max_depth < 20 else "FLOOD_RISK",
                        severity=sev,
                        expected_depth_cm=round(max_depth, 1),
                        rainfall_intensity_mmhr=round(rainfall_now, 1),
                        timestep_minutes=peak_timestep_min,
                        horizon_label=horizon_label,
                        drainage_stress_ratio=round(peak_stress, 2),
                        location_name=loc.label,
                        source="CITIZEN_ENGINE",
                        predicted_event_time=impact_time,
                        alert_sent_time=now,
                        status="SENT",
                        deduplication_key=dedup_key,
                        is_read=False,
                    )
                    db.add(new_alert)
                    db.commit()
                    db.refresh(new_alert)
                    generated_or_updated_alerts.append(new_alert)

        return generated_or_updated_alerts


early_warning_service = EarlyWarningService()
