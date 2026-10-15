"""DrainIQ - Smart Community Emergency and Disaster Alert Network (SS-03) REST API Server."""

from contextlib import asynccontextmanager
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.app.api.v1.api import api_router
from backend.app.config import settings
from backend.app.core.database import init_db
from backend.app.core.exceptions import register_exception_handlers
from backend.app.core.rate_limit import limiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler initializing SQLite/PostgreSQL schemas on startup."""
    init_db()
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="REST API for DrainIQ Smart Community Emergency & Disaster Alert Network (SS-03). "
                    "Powers hydraulic nowcasts, vehicle safe routing, citizen early warning, "
                    "crowd-sourced hazard corroboration, and municipal emergency dispatch.",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(
        GZipMiddleware,
        minimum_size=1000,
    )

    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time_ms = (time.time() - start_time) * 1000.0
        response.headers["X-Process-Time-Ms"] = f"{process_time_ms:.2f}"
        return response

    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    def api_overview_payload() -> dict:
        return {
            "system": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "online",
            "city": "Lucknow, Uttar Pradesh",
            "api_v1_prefix": settings.API_V1_PREFIX,
            "documentation": {
                "swagger_ui": "/docs",
                "redoc": "/redoc",
                "openapi_json": "/openapi.json",
            },
            "modules": {
                "health": f"{settings.API_V1_PREFIX}/health",
                "auth": f"{settings.API_V1_PREFIX}/auth/me",
                "citizen": f"{settings.API_V1_PREFIX}/citizen/notifications",
                "municipal": f"{settings.API_V1_PREFIX}/municipal/nowcast",
                "emergency": f"{settings.API_V1_PREFIX}/emergency/shelters",
                "routing": f"{settings.API_V1_PREFIX}/routing/presets",
                "flood_map": f"{settings.API_V1_PREFIX}/map/scenarios",
                "simulation": f"{settings.API_V1_PREFIX}/simulation/metrics",
            },
        }

    @app.get("/", summary="DrainIQ Root Overview", tags=["System"])
    @app.get("/api-info", summary="DrainIQ Root Overview", tags=["System"])
    async def root():
        """Stable JSON system overview for developers and clients."""
        return JSONResponse(api_overview_payload())

    return app


app = create_app()
