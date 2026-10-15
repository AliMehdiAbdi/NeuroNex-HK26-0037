"""Central API v1 router mounting all modular endpoint collections."""

from fastapi import APIRouter

from backend.app.api.v1.endpoints import (
    auth,
    citizen,
    emergency,
    flood_map,
    health,
    municipal,
    routing,
    simulation,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(citizen.router)
api_router.include_router(municipal.router)
api_router.include_router(emergency.router)
api_router.include_router(routing.router)
api_router.include_router(flood_map.router)
api_router.include_router(simulation.router)
