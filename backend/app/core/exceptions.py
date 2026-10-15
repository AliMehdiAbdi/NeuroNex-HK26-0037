"""Global Custom Exceptions & Handlers for DrainIQ."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class ZoneNotFoundException(Exception):
    def __init__(self, zone: str):
        self.zone = zone
        self.message = f"Zone '{zone}' is not supported. Choose from 'pilot' or 'validation'."
        super().__init__(self.message)


class ScenarioNotFoundException(Exception):
    def __init__(self, scenario: str):
        self.scenario = scenario
        self.message = f"Scenario '{scenario}' not found."
        super().__init__(self.message)


class AssetNotFoundException(Exception):
    def __init__(self, asset_name: str):
        self.asset_name = asset_name
        self.message = f"Data file '{asset_name}' not found."
        super().__init__(self.message)


class RoutingException(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class AuthException(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def register_exception_handlers(app: FastAPI) -> None:
    """Registers standard JSON exception handlers on the FastAPI application."""

    @app.exception_handler(ZoneNotFoundException)
    async def zone_not_found_handler(request: Request, exc: ZoneNotFoundException):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "ZoneNotFound", "message": exc.message, "zone": exc.zone},
        )

    @app.exception_handler(ScenarioNotFoundException)
    async def scenario_not_found_handler(request: Request, exc: ScenarioNotFoundException):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "ScenarioNotFound", "message": exc.message, "scenario": exc.scenario},
        )

    @app.exception_handler(AssetNotFoundException)
    async def asset_not_found_handler(request: Request, exc: AssetNotFoundException):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "AssetNotFound", "message": exc.message, "asset": exc.asset_name},
        )

    @app.exception_handler(RoutingException)
    async def routing_error_handler(request: Request, exc: RoutingException):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "RoutingError", "message": exc.detail},
        )

    @app.exception_handler(AuthException)
    async def auth_error_handler(request: Request, exc: AuthException):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "AuthError", "message": exc.detail},
        )
