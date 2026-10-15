"""Common schemas used across DrainIQ API endpoints."""

from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class MessageResponse(BaseModel):
    """Generic status message response."""

    message: str
    detail: Optional[str] = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated list wrapper."""

    total_count: int
    page: int = 1
    page_size: int = 50
    items: List[T]


class HealthResponse(BaseModel):
    """System health and runtime status."""

    status: str
    version: str
    city: str
    supported_zones: List[str]
    default_zone: str
    server_time_iso: str
    total_geojson_assets_indexed: int
    scenarios_indexed_count: int
    cache_stats: Any = None

