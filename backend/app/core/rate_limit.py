"""API rate limiter configuration using slowapi."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.app.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings.RATE_LIMIT_ENABLED,
    headers_enabled=True,
    default_limits=[settings.DEFAULT_RATE_LIMIT],
)
