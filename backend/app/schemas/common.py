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
