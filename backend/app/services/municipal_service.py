"""Municipal operational command, priority hotspots, bottlenecks, and infrastructure exposure."""

from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any, Dict, List, Optional
import numpy as np
from scipy.spatial import cKDTree
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.citizen import Notification
from backend.app.models.municipal import CriticalInfrastructure, Hotspot, HotspotAction
from backend.app.models.user import User
from backend.app.schemas.municipal import (
    BroadcastAlertResponse,
    CriticalInfrastructureItem,
    CriticalInfrastructureResponse,
    DrainageBottleneckItem,
    DrainageProblemResponse,
    HotspotActionResponse,
    HotspotActionSummary,
    HotspotDetailResponse,
    MunicipalNowcastResponse,
    NowcastHorizonItem,
)
from backend.app.services.file_index import file_index
from backend.app.services.geojson_loader import geojson_loader
from backend.app.services.routing_service import geographic_distance_m
from backend.app.services.street_name_service import street_name_service
from backend.app.services.timeline_loader import timeline_loader


class MunicipalCommandService:
    """Operational intelligence and mitigation analytics for municipal coordinators."""

    def __init__(self):
        self._infra_data: Optional[List[Dict[str, Any]]] = None
        self._load_infrastructure_catalog()

    def _load_infrastructure_catalog(self) -> None:
        infra_path = os.path.join(settings.CATALOGS_DIR, "lucknow_critical_infrastructure.json")
        if os.path.exists(infra_path):
            try:
                with open(infra_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._infra_data = data.get("items", [])
            except Exception:
                self._infra_data = []

    def get_0_to_3hr_nowcast(self, zone: str, scenario: str) -> MunicipalNowcastResponse:
        """Computes 0-3 hour nowcast projections across discrete timesteps."""
        z = file_index.validate_zone(zone)
        slug = file_index.resolve_slug(scenario)
        timeline_doc = timeline_loader.load_timeline(z, slug)
        steps = timeline_doc.get("timeline", [])

        horizons = [
            ("NOW", 0.0),
            ("+30 MIN", 30.0),
            ("+1 HOUR", 60.0),
            ("+2 HOURS", 120.0),
            ("+3 HOURS", 180.0),
        ]

        horizon_items: List[NowcastHorizonItem] = []
        now_dt = datetime.now(timezone.utc)

        for label, target_t in horizons:
            matched_step = min(steps, key=lambda s: abs(s["time_minutes"] - target_t))
            depth = float(matched_step.get("max_depth_cm", 0.0))
            rain = float(matched_step.get("rainfall_intensity_mmhr", 0.0))
            overloaded = float(matched_step.get("overloaded_nodes_pct", 0.0))
            frs = float(matched_step.get("zone_composite_frs", 0.0))
            risk_cat = str(matched_step.get("zone_risk_category", "LOW"))
            conf = 88.0 if target_t <= 30 else (82.0 if target_t <= 60 else (74.0 if target_t <= 120 else 66.0))

            horizon_items.append(
                NowcastHorizonItem(
                    horizon_label=label,
                    timestep_minutes=target_t,
                    rainfall_intensity_mmhr=round(rain, 1),
                    max_depth_cm=round(depth, 1),
                    mean_depth_cm=round(float(matched_step.get("mean_depth_cm", 0.0)), 1),
                    overloaded_nodes_pct=round(overloaded, 1),
                    impassable_road_length_km=round(float(matched_step.get("impassable_road_length_km", 0.0)), 2),
                    zone_composite_frs=round(frs, 2),
                    zone_risk_category=risk_cat,
                    confidence_pct=conf,
                )
            )

        sc_name = timeline_doc.get("scenario", {}).get("name", slug.replace("_", " ").title())

        return MunicipalNowcastResponse(
            zone=z,
            scenario_id=slug.upper(),
            scenario_name=sc_name,
            current_time_iso=now_dt.isoformat(),
            timeline=horizon_items,
        )

    def get_drainage_problems(self, zone: str, scenario: str, t: float = 45.0) -> DrainageProblemResponse:
        """Diagnose surcharged manholes, pipe capacity bottlenecks, and drainage choke points."""
        z = file_index.validate_zone(zone)
        slug = file_index.resolve_slug(scenario)

        nodes_fc = geojson_loader.load_layer(z, slug, t, "nodes")
        edges_fc = geojson_loader.load_layer(z, slug, t, "edges")

        nodes = nodes_fc.get("features", [])
        edges = edges_fc.get("features", [])

        surcharged_nodes = []
        bottlenecks = []

        for f in nodes:
            props = f.get("properties", {})
            stress = float(props.get("stress_ratio", 0.0))
            surcharge = float(props.get("surcharge_m3s", 0.0))
            depth = float(props.get("depth_cm", 0.0))
            nid = str(props.get("node_id", ""))

            if stress > 1.0 or surcharge > 0.0:
                surcharged_nodes.append(nid)

            if depth > 10.0 and (stress > 1.2 or bool(props.get("is_local_min", False))):
                elev = float(props.get("terrain_m", 112.0))
                cause = "Local Terrain Sink & Conduit Surcharge" if props.get("is_local_min") else "Capacity Overload (Inflow > Pipe Capacity)"
                bottlenecks.append(
                    DrainageBottleneckItem(
                        node_id=nid,
                        locality=f"Sector Manhole #{nid}",
                        elevation_m=elev,
                        drainage_stress_ratio=round(stress, 2),
                        surcharge_m3s=round(surcharge, 3),
                        pipe_capacity_m3s=round(float(props.get("capacity_m3s", 0.8)), 2),
                        bottleneck_cause=cause,
                    )
                )

        bottlenecks = sorted(bottlenecks, key=lambda b: b.drainage_stress_ratio, reverse=True)[:20]
        total_nodes = max(len(nodes), 1)
        surcharged_pct = (len(surcharged_nodes) / total_nodes) * 100.0
        conduits_over = sum(1 for e in edges if float(e.get("properties", {}).get("depth_cm", 0.0)) > 15.0)

        return DrainageProblemResponse(
            zone=z,
            timestep_minutes=t,
            total_manholes=len(nodes),
            surcharged_manholes_count=len(surcharged_nodes),
            surcharged_percentage=round(surcharged_pct, 1),
            total_conduits=len(edges),
            conduits_over_capacity_count=conduits_over,
            bottlenecks=bottlenecks,
        )

    def get_priority_hotspots(
        self,
        db: Session,
        zone: str,
        scenario: str,
        t: float = 45.0,
        min_risk: str = "ALL",
    ) -> List[HotspotDetailResponse]:
        """Rank and return flood risk hotspots with mitigation suggestions and recent actions."""
        z = file_index.validate_zone(zone)
        slug = file_index.resolve_slug(scenario)

        nodes_fc = geojson_loader.load_layer(z, slug, t, "nodes")
        timeline_doc = timeline_loader.load_timeline(z, slug)

        step = min(timeline_doc.get("timeline", []), key=lambda s: abs(s["time_minutes"] - t))
        rain_mmhr = float(step.get("rainfall_intensity_mmhr", 0.0))

        nodes = nodes_fc.get("features", [])
        sorted_nodes = sorted(
            nodes,
            key=lambda f: (
                float(f["properties"].get("depth_cm", 0.0)),
                float(f["properties"].get("frs_score", 0.0)),
            ),
            reverse=True,
        )

        hotspot_details: List[HotspotDetailResponse] = []
        street_names = street_name_service.get_street_names(z)
        now_dt = datetime.now(timezone.utc)

        landmarks = {
            "Charbagh Railway Station": [80.9231, 26.8324],
            "Hazratganj Commercial Hub": [80.9448, 26.8548],
            "Aminabad Bazaar": [80.9240, 26.8510],
            "Nirala Nagar Depression": [80.9357, 26.8790],
            "Aliganj Commercial Road": [80.9480, 26.9051],
            "Gomti Nagar Vibhuti Khand": [80.9989, 26.8529],
            "Vikas Nagar Drain Sump": [80.9641, 26.8942],
            "Rumi Darwaza Heritage Corridor": [80.9122, 26.8712],
            "Mawaiya Underpass Corridor": [80.9120, 26.8280],
        }

        for rank, f in enumerate(sorted_nodes[:20], 1):
            props = f.get("properties", {})
            coords = f.get("geometry", {}).get("coordinates", [80.94, 26.85])
            lon, lat = float(coords[0]), float(coords[1])
            depth = float(props.get("depth_cm", 0.0))
            frs = float(props.get("frs_score", 0.0))
            stress = float(props.get("stress_ratio", 0.0))
            is_sink = bool(props.get("is_local_min", False))

            if depth > 30.0 or frs >= 0.85:
                cat = "CRITICAL"
            elif depth > 15.0 or frs >= 0.70:
                cat = "HIGH"
            elif depth >= 5.0 or frs >= 0.40:
                cat = "MODERATE"
            else:
                cat = "LOW"

            if min_risk != "ALL" and cat != min_risk:
                continue

            nearest_name = "Central Ward"
            min_dist = float("inf")
            for lm_name, lm_coords in landmarks.items():
                d_m = geographic_distance_m((lon, lat), (lm_coords[0], lm_coords[1]))
                if d_m < min_dist:
                    min_dist = d_m
                    nearest_name = f"{lm_name} (~{d_m:.0f}m)"

            factors = []
            if rain_mmhr >= 40:
                factors.append(f"Heavy Rain ({rain_mmhr:.0f} mm/hr)")
            if is_sink:
                factors.append("Low Topographic Sink")
            if stress >= 1.0:
                factors.append(f"Drainage Overload ({stress*100:.0f}%)")
            else:
                factors.append("Surface Impervious Runoff")

            if cat in ["HIGH", "CRITICAL"]:
                rec = "DEPLOY_PUMP | TRAFFIC_DIVERSION | FIELD_CREW"
            elif cat == "MODERATE":
                rec = "INSPECT_DRAINAGE | CLEAR_MANHOLES"
            else:
                rec = "ROUTINE_MONITORING"

            nid = str(props.get("node_id", ""))
            street_candidates = street_names.get(nid, []) if nid else []
            road_name = ", ".join(street_candidates) if street_candidates else f"Sector Node {nid or rank}"

            hotspot_row = (
                db.query(Hotspot)
                .filter(Hotspot.zone == z, Hotspot.node_id == nid)
                .first()
            )
            if hotspot_row is None:
                hotspot_row = Hotspot(zone=z, node_id=nid, detected_at=now_dt)
                db.add(hotspot_row)

            hotspot_row.locality_name = nearest_name
            hotspot_row.road_name = road_name
            hotspot_row.street_names = ", ".join(street_candidates)
            hotspot_row.latitude = lat
            hotspot_row.longitude = lon
            hotspot_row.frs_score = round(frs, 2)
            hotspot_row.risk_category = cat
            hotspot_row.predicted_depth_cm = round(depth, 1)
            hotspot_row.drainage_stress_ratio = round(stress, 2)
            hotspot_row.rainfall_intensity_mmhr = round(rain_mmhr, 1)
            hotspot_row.confidence_pct = 84.0 if depth > 10 else 76.0
            hotspot_row.primary_contributing_factors = " | ".join(factors)
            hotspot_row.suggested_response_category = rec
            db.flush()

            recent_actions = [
                HotspotActionSummary(
                    id=act.id,
                    user_name=act.user.full_name if act.user else None,
                    action_taken=act.action_taken,
                    notes=act.notes,
                    action_timestamp=act.action_timestamp,
                )
                for act in sorted(
                    hotspot_row.actions, key=lambda a: a.action_timestamp, reverse=True
                )[:5]
            ]

            hotspot_details.append(
                HotspotDetailResponse(
                    id=hotspot_row.id,
                    zone=z,
                    node_id=nid or None,
                    locality_name=nearest_name,
                    road_name=road_name,
                    street_names=hotspot_row.street_names,
                    latitude=lat,
                    longitude=lon,
                    frs_score=round(frs, 2),
                    risk_category=cat,
                    predicted_depth_cm=round(depth, 1),
                    drainage_stress_ratio=round(stress, 2),
                    rainfall_intensity_mmhr=round(rain_mmhr, 1),
                    confidence_pct=84.0 if depth > 10 else 76.0,
                    notice_status=hotspot_row.notice_status,
                    primary_contributing_factors=hotspot_row.primary_contributing_factors,
                    suggested_response_category=rec,
                    detected_at=now_dt,
                    recent_actions=recent_actions,
                )
            )

        db.commit()
        return hotspot_details

    def log_hotspot_action(
        self, db: Session, hotspot_id: int, user_id: Optional[int], action_taken: str, notes: Optional[str] = None
    ) -> HotspotActionResponse:
        """Record operational response against a flood hotspot."""
        hotspot = db.query(Hotspot).filter(Hotspot.id == hotspot_id).first()
        if not hotspot:
            raise ValueError(f"Hotspot ID {hotspot_id} not found.")

        action = HotspotAction(
            hotspot_id=hotspot_id,
            user_id=user_id,
            action_taken=action_taken,
            notes=notes,
            action_timestamp=datetime.now(timezone.utc),
        )
        db.add(action)
        db.commit()
        db.refresh(action)

        user_name = action.user.full_name if action.user else None
        return HotspotActionResponse(
            id=action.id,
            hotspot_id=action.hotspot_id,
            user_id=action.user_id,
            user_name=user_name,
            action_taken=action.action_taken,
            notes=action.notes,
            action_timestamp=action.action_timestamp,
        )

    def get_critical_infrastructure_exposure(
        self, zone: str, scenario: str, t: float = 45.0
    ) -> CriticalInfrastructureResponse:
        """Evaluate proximity and flood depth near hospitals, schools, and transit nodes."""
        z = file_index.validate_zone(zone)
        slug = file_index.resolve_slug(scenario)

        nodes_fc = geojson_loader.load_layer(z, slug, t, "nodes")
        nodes = nodes_fc.get("features", [])

        node_coords = []
        node_depths = []
        for f in nodes:
            coords = f.get("geometry", {}).get("coordinates", [])
            if coords and len(coords) >= 2:
                node_coords.append([float(coords[0]), float(coords[1])])
                node_depths.append(float(f.get("properties", {}).get("depth_cm", 0.0)))

        items: List[CriticalInfrastructureItem] = []
        critical_cnt = 0

        if node_coords and self._infra_data:
            tree = cKDTree(np.array(node_coords))
            depths_arr = np.array(node_depths)

            for infra in self._infra_data:
                ilon, ilat = infra["longitude"], infra["latitude"]
                dists, idxs = tree.query([ilon, ilat], k=min(3, len(node_coords)))
                if isinstance(dists, float):
                    dists = [dists]
                    idxs = [idxs]

                min_dist_m = float(min(dists)) * 111000.0
                closest_node_idx = idxs[np.argmin(dists)]
                pred_depth = float(depths_arr[closest_node_idx])

                if pred_depth >= 30.0:
                    exp = "CRITICAL"
                    critical_cnt += 1
                elif pred_depth >= 15.0:
                    exp = "HIGH"
                    critical_cnt += 1
                elif pred_depth >= 5.0:
                    exp = "MODERATE"
                elif min_dist_m <= 300.0 and pred_depth > 0.0:
                    exp = "LOW"
                else:
                    exp = "NONE"

                items.append(
                    CriticalInfrastructureItem(
                        id=infra["id"],
                        name=infra["name"],
                        category=infra["category"],
                        latitude=ilat,
                        longitude=ilon,
                        zone=infra.get("zone", z),
                        baseline_elevation_m=infra.get("baseline_elevation_m", 115.0),
                        distance_to_nearest_hotspot_m=round(min_dist_m),
                        predicted_flood_depth_cm=round(pred_depth, 1),
                        exposure_level=exp,
                    )
                )

        return CriticalInfrastructureResponse(
            total_count=len(items),
            critical_count=critical_cnt,
            items=items,
        )

    def broadcast_alert(
        self,
        db: Session,
        title: str,
        message: str,
        zone: str = "pilot",
        severity: str = "HIGH",
        target_channel: str = "ALL",
    ) -> BroadcastAlertResponse:
        """Publish emergency broadcast notification to all registered citizens."""
        now = datetime.now(timezone.utc)
        users = db.query(User).filter(User.is_active == True).all()
        recipients_count = 0

        for user in users:
            pref = user.notification_preference
            if pref and not pref.enable_emergency_broadcasts:
                continue

            alert = Notification(
                user_id=user.id,
                title=f"EMERGENCY BROADCAST: {title}",
                message=message,
                event_category="BROADCAST",
                hazard_type="EMERGENCY_BROADCAST",
                severity=severity,
                expected_depth_cm=0.0,
                alert_sent_time=now,
                status="SENT",
                source="MUNICIPAL_COMMAND",
                is_read=False,
            )
            db.add(alert)
            recipients_count += 1

        db.commit()
        broadcast_id = f"BC-{now.strftime('%Y%m%d%H%M%S')}"

        return BroadcastAlertResponse(
            status="SUCCESS",
            broadcast_id=broadcast_id,
            recipients_count=recipients_count,
            sent_at=now,
        )


municipal_service = MunicipalCommandService()
