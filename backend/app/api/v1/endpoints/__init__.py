"""Endpoints package for DrainIQ API v1."""

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

__all__ = [
    "auth",
    "citizen",
    "emergency",
    "flood_map",
    "health",
    "municipal",
    "routing",
    "simulation",
]
