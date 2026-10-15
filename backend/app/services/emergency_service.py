"""Emergency disaster operations, evacuation shelters, incident crowd-verification, and SOS rescue dispatch (SS-03)."""

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.models.emergency import (
    AssistanceRequest,
    EmergencyShelter,
    IncidentReport,
    IncidentVerification,
    ReliefResource,
)
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
    IncidentVerificationResponse,
    ReliefResourceCreate,
    ReliefResourceResponse,
)
from backend.app.services.routing_service import geographic_distance_m


class EmergencyOperationsService:
    """Disaster alert network and response coordinator (SS-03)."""

    # --- Shelters ---

    def list_shelters(
        self, db: Session, zone: str = "pilot", status: Optional[str] = None
    ) -> List[EmergencyShelterResponse]:
        """List designated evacuation shelters with live occupancy calculations."""
        query = db.query(EmergencyShelter).filter(EmergencyShelter.zone == zone)
        if status:
            query = query.filter(EmergencyShelter.status == status)

        shelters = query.all()
        results: List[EmergencyShelterResponse] = []
        for s in shelters:
            occ_pct = round((s.current_occupancy / max(s.capacity, 1)) * 100.0, 1)
            results.append(
                EmergencyShelterResponse(
                    id=s.id,
                    name=s.name,
                    address=s.address,
                    latitude=s.latitude,
                    longitude=s.longitude,
                    zone=s.zone,
                    capacity=s.capacity,
                    current_occupancy=s.current_occupancy,
                    occupancy_pct=occ_pct,
                    status=s.status,
                    amenities=s.amenities,
                    contact_person=s.contact_person,
                    contact_phone=s.contact_phone,
                    created_at=s.created_at,
                )
            )
        return results

    def create_shelter(self, db: Session, payload: EmergencyShelterCreate) -> EmergencyShelterResponse:
        """Register a new emergency shelter."""
        shelter = EmergencyShelter(
            name=payload.name,
            address=payload.address,
            latitude=payload.latitude,
            longitude=payload.longitude,
            zone=payload.zone,
            capacity=payload.capacity,
            current_occupancy=0,
            status="OPEN",
            amenities=payload.amenities,
            contact_person=payload.contact_person,
            contact_phone=payload.contact_phone,
            created_at=datetime.now(timezone.utc),
        )
        db.add(shelter)
        db.commit()
        db.refresh(shelter)

        return EmergencyShelterResponse(
            id=shelter.id,
            name=shelter.name,
            address=shelter.address,
            latitude=shelter.latitude,
            longitude=shelter.longitude,
            zone=shelter.zone,
            capacity=shelter.capacity,
            current_occupancy=shelter.current_occupancy,
            occupancy_pct=0.0,
            status=shelter.status,
            amenities=shelter.amenities,
            contact_person=shelter.contact_person,
            contact_phone=shelter.contact_phone,
            created_at=shelter.created_at,
        )

    def update_shelter(
        self, db: Session, shelter_id: int, payload: EmergencyShelterUpdate
    ) -> EmergencyShelterResponse:
        """Update shelter occupancy, capacity, or operational status."""
        shelter = db.query(EmergencyShelter).filter(EmergencyShelter.id == shelter_id).first()
        if not shelter:
            raise ValueError(f"Shelter ID {shelter_id} not found.")

        if payload.capacity is not None:
            shelter.capacity = payload.capacity
        if payload.current_occupancy is not None:
            shelter.current_occupancy = payload.current_occupancy
            if shelter.current_occupancy >= shelter.capacity:
                shelter.status = "FULL"
            elif shelter.current_occupancy >= shelter.capacity * 0.9:
                shelter.status = "NEAR_CAPACITY"
            elif shelter.status in ["FULL", "NEAR_CAPACITY"]:
                shelter.status = "OPEN"
        if payload.status is not None:
            shelter.status = payload.status
        if payload.amenities is not None:
            shelter.amenities = payload.amenities
        if payload.contact_person is not None:
            shelter.contact_person = payload.contact_person
        if payload.contact_phone is not None:
            shelter.contact_phone = payload.contact_phone

        db.commit()
        db.refresh(shelter)

        occ_pct = round((shelter.current_occupancy / max(shelter.capacity, 1)) * 100.0, 1)
        return EmergencyShelterResponse(
            id=shelter.id,
            name=shelter.name,
            address=shelter.address,
            latitude=shelter.latitude,
            longitude=shelter.longitude,
            zone=shelter.zone,
            capacity=shelter.capacity,
            current_occupancy=shelter.current_occupancy,
            occupancy_pct=occ_pct,
            status=shelter.status,
            amenities=shelter.amenities,
            contact_person=shelter.contact_person,
            contact_phone=shelter.contact_phone,
            created_at=shelter.created_at,
        )

    def find_nearest_shelter(
        self, db: Session, lon: float, lat: float, zone: str = "pilot"
    ) -> Optional[Tuple[EmergencyShelterResponse, float]]:
        """Find the closest open shelter with available capacity."""
        shelters = self.list_shelters(db, zone=zone)
        open_shelters = [s for s in shelters if s.status in ["OPEN", "NEAR_CAPACITY"]]
        if not open_shelters:
            return None

        best_shelter: Optional[EmergencyShelterResponse] = None
        min_distance_m = float("inf")

        for s in open_shelters:
            dist = geographic_distance_m((lon, lat), (s.longitude, s.latitude))
            if dist < min_distance_m:
                min_distance_m = dist
                best_shelter = s

        if best_shelter:
            return best_shelter, round(min_distance_m)
        return None

    # --- Relief Resources ---

    def list_resources(
        self, db: Session, category: Optional[str] = None, status: Optional[str] = None
    ) -> List[ReliefResourceResponse]:
        """List field relief assets and emergency supplies."""
        query = db.query(ReliefResource)
        if category:
            query = query.filter(ReliefResource.category == category)
        if status:
            query = query.filter(ReliefResource.status == status)

        resources = query.all()
        return [
            ReliefResourceResponse(
                id=r.id,
                name=r.name,
                category=r.category,
                latitude=r.latitude,
                longitude=r.longitude,
                status=r.status,
                available_units=r.available_units,
                contact_phone=r.contact_phone,
                updated_at=r.updated_at,
            )
            for r in resources
        ]

    def create_resource(self, db: Session, payload: ReliefResourceCreate) -> ReliefResourceResponse:
        """Register relief deployment."""
        res = ReliefResource(
            name=payload.name,
            category=payload.category,
            latitude=payload.latitude,
            longitude=payload.longitude,
            status=payload.status,
            available_units=payload.available_units,
            contact_phone=payload.contact_phone,
            updated_at=datetime.now(timezone.utc),
        )
        db.add(res)
        db.commit()
        db.refresh(res)
        return ReliefResourceResponse(
            id=res.id,
            name=res.name,
            category=res.category,
            latitude=res.latitude,
            longitude=res.longitude,
            status=res.status,
            available_units=res.available_units,
            contact_phone=res.contact_phone,
            updated_at=res.updated_at,
        )

    # --- Incident Reporting & Verification ---

    def report_incident(
        self, db: Session, user_id: Optional[int], payload: IncidentReportCreate
    ) -> IncidentReportResponse:
        """Citizen submission for localized flood hazards and waterlogging."""
        incident = IncidentReport(
            user_id=user_id,
            hazard_type=payload.hazard_type,
            title=payload.title,
            description=payload.description,
            severity=payload.severity,
            latitude=payload.latitude,
            longitude=payload.longitude,
            address_description=payload.address_description,
            photo_url=payload.photo_url,
            status="PENDING",
            confirmations_count=1 if user_id else 0,
            disputes_count=0,
            trust_score=1.0,
            reported_at=datetime.now(timezone.utc),
        )
        db.add(incident)
        db.commit()
        db.refresh(incident)

        return self._format_incident(incident)

    def list_incidents(
        self,
        db: Session,
        hazard_type: Optional[str] = None,
        status: Optional[str] = None,
        min_severity: Optional[str] = None,
    ) -> List[IncidentReportResponse]:
        """List reported incidents with corroboration metrics."""
        query = db.query(IncidentReport)
        if hazard_type:
            query = query.filter(IncidentReport.hazard_type == hazard_type)
        if status:
            query = query.filter(IncidentReport.status == status)
        if min_severity:
            query = query.filter(IncidentReport.severity == min_severity)

        incidents = query.order_by(IncidentReport.reported_at.desc()).all()
        return [self._format_incident(inc) for inc in incidents]

    def verify_incident(
        self, db: Session, incident_id: int, user_id: int, payload: IncidentVerificationCreate
    ) -> IncidentReportResponse:
        """Corroborate or dispute a citizen incident report with trust score recalculation."""
        incident = db.query(IncidentReport).filter(IncidentReport.id == incident_id).first()
        if not incident:
            raise ValueError(f"Incident ID {incident_id} not found.")

        existing_vote = (
            db.query(IncidentVerification)
            .filter(
                IncidentVerification.incident_id == incident_id,
                IncidentVerification.user_id == user_id,
            )
            .first()
        )
        if existing_vote:
            if existing_vote.vote_type == "CONFIRM":
                incident.confirmations_count = max(0, incident.confirmations_count - 1)
            elif existing_vote.vote_type == "DISPUTE":
                incident.disputes_count = max(0, incident.disputes_count - 1)

            existing_vote.vote_type = payload.vote_type
            existing_vote.comment = payload.comment
            existing_vote.timestamp = datetime.now(timezone.utc)
        else:
            vote = IncidentVerification(
                incident_id=incident_id,
                user_id=user_id,
                vote_type=payload.vote_type,
                comment=payload.comment,
                timestamp=datetime.now(timezone.utc),
            )
            db.add(vote)

        if payload.vote_type == "CONFIRM":
            incident.confirmations_count += 1
        elif payload.vote_type == "DISPUTE":
            incident.disputes_count += 1

        total_votes = incident.confirmations_count + incident.disputes_count
        if total_votes > 0:
            incident.trust_score = round(
                (incident.confirmations_count + 1) / (total_votes + 1), 2
            )

        # Automatic consensus state transition
        if incident.confirmations_count >= 3 and incident.status == "PENDING":
            incident.status = "VERIFIED"
        elif incident.disputes_count >= 3 and incident.disputes_count > incident.confirmations_count * 2:
            incident.status = "FALSE_REPORT"

        db.commit()
        db.refresh(incident)
        return self._format_incident(incident)

    def _format_incident(self, inc: IncidentReport) -> IncidentReportResponse:
        verifs = [
            IncidentVerificationResponse(
                id=v.id,
                incident_id=v.incident_id,
                user_id=v.user_id,
                vote_type=v.vote_type,
                comment=v.comment,
                timestamp=v.timestamp,
            )
            for v in sorted(inc.verifications, key=lambda x: x.timestamp, reverse=True)[:10]
        ]
        return IncidentReportResponse(
            id=inc.id,
            user_id=inc.user_id,
            hazard_type=inc.hazard_type,
            title=inc.title,
            description=inc.description,
            severity=inc.severity,
            latitude=inc.latitude,
            longitude=inc.longitude,
            address_description=inc.address_description,
            photo_url=inc.photo_url,
            status=inc.status,
            confirmations_count=inc.confirmations_count,
            disputes_count=inc.disputes_count,
            trust_score=inc.trust_score,
            reported_at=inc.reported_at,
            resolved_at=inc.resolved_at,
            verifications=verifs,
        )

    # --- SOS Assistance Requests ---

    def submit_sos(
        self, db: Session, user_id: Optional[int], payload: AssistanceRequestCreate
    ) -> AssistanceRequestResponse:
        """Lodge urgent rescue request for stranded residents."""
        sos = AssistanceRequest(
            user_id=user_id,
            requester_name=payload.requester_name,
            contact_phone=payload.contact_phone,
            request_type=payload.request_type,
            urgency_level=payload.urgency_level,
            latitude=payload.latitude,
            longitude=payload.longitude,
            address_details=payload.address_details,
            stranded_count=payload.stranded_count,
            vulnerability_flags=payload.vulnerability_flags,
            notes=payload.notes,
            status="PENDING",
            requested_at=datetime.now(timezone.utc),
        )
        db.add(sos)
        db.commit()
        db.refresh(sos)
        return self._format_sos(sos)

    def list_sos(
        self, db: Session, status: Optional[str] = None, urgency: Optional[str] = None
    ) -> List[AssistanceRequestResponse]:
        """List active SOS emergency requests sorted by urgency level."""
        query = db.query(AssistanceRequest)
        if status:
            query = query.filter(AssistanceRequest.status == status)
        if urgency:
            query = query.filter(AssistanceRequest.urgency_level == urgency)

        requests = query.order_by(
            AssistanceRequest.urgency_level.desc(), AssistanceRequest.requested_at.asc()
        ).all()
        return [self._format_sos(s) for s in requests]

    def update_sos(
        self, db: Session, request_id: int, payload: AssistanceRequestUpdate
    ) -> AssistanceRequestResponse:
        """Update dispatch status and assigned emergency team."""
        sos = db.query(AssistanceRequest).filter(AssistanceRequest.id == request_id).first()
        if not sos:
            raise ValueError(f"Assistance request {request_id} not found.")

        if payload.status is not None:
            sos.status = payload.status
            if payload.status == "RESOLVED":
                sos.resolved_at = datetime.now(timezone.utc)
        if payload.assigned_team is not None:
            sos.assigned_team = payload.assigned_team
        if payload.coordinator_notes is not None:
            sos.coordinator_notes = payload.coordinator_notes

        db.commit()
        db.refresh(sos)
        return self._format_sos(sos)

    def _format_sos(self, s: AssistanceRequest) -> AssistanceRequestResponse:
        return AssistanceRequestResponse(
            id=s.id,
            user_id=s.user_id,
            requester_name=s.requester_name,
            contact_phone=s.contact_phone,
            request_type=s.request_type,
            urgency_level=s.urgency_level,
            latitude=s.latitude,
            longitude=s.longitude,
            address_details=s.address_details,
            stranded_count=s.stranded_count,
            vulnerability_flags=s.vulnerability_flags,
            notes=s.notes,
            status=s.status,
            assigned_team=s.assigned_team,
            coordinator_notes=s.coordinator_notes,
            requested_at=s.requested_at,
            resolved_at=s.resolved_at,
        )


emergency_service = EmergencyOperationsService()
