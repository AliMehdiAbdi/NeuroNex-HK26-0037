"""Application configuration using Pydantic Settings."""

import os
from typing import Any, Dict, List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class Settings(BaseSettings):
    """Global configuration settings for DrainIQ."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        env_file=(
            os.path.join(_BASE_DIR, ".env"),
            os.path.join(_BASE_DIR, ".env.local"),
        ),
        env_file_encoding="utf-8",
    )

    # Server Settings
    APP_NAME: str = "DrainIQ - Emergency & Disaster Alert Network"
    APP_VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    BASE_DIR: str = _BASE_DIR

    # Data paths relative to workspace root
    DATA_DIR: str = os.path.join(BASE_DIR, "backend", "data")
    CATALOGS_DIR: str = os.path.join(DATA_DIR, "catalogs")
    SPATIAL_PILOT_DIR: str = os.path.join(DATA_DIR, "spatial", "pilot")
    SPATIAL_VALIDATION_DIR: str = os.path.join(DATA_DIR, "spatial", "validation")
    OUTPUTS_DIR: str = os.path.join(DATA_DIR, "outputs")
    ENGINE_OUTPUTS_DIR: str = os.path.join(DATA_DIR, "outputs")
    DRAINAGE_DATA_DIR: str = os.path.join(DATA_DIR, "catalogs")

    # In-memory Cache Limits
    GEOJSON_CACHE_MAXSIZE: int = 128
    TIMELINE_CACHE_MAXSIZE: int = 64

    # Supported Zones & Scenarios
    DEFAULT_ZONE: str = "pilot"
    SUPPORTED_ZONES: List[str] = ["pilot", "validation"]
    DEFAULT_SCENARIO: str = "CPHEEO-EXTREME-60MM"
    STANDARD_TIMESTEPS_MIN: List[float] = [0.0, 15.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0]

    # Vehicle Ground Clearance Thresholds (cm)
    VEHICLE_CLEARANCES_CM: Dict[str, float] = {
        "pedestrian": 5.0,
        "two_wheeler": 10.0,
        "car": 15.0,
        "emergency_suv": 28.0,
        "ambulance": 30.0,
        "emergency": 30.0,
        "bus": 35.0,
    }

    # Calibration & Ground-Truth Benchmarks
    CALIBRATION_BENCHMARKS: Dict[str, Any] = {
        "ground_truth_sites_count": 8,
        "proximity_p_value": 0.013,
        "scientific_confidence_pct": 98.7,
        "site_ranking_percentile": 70.2,
        "model_mean_distance_to_known_floods_km": 0.69,
        "random_chance_distance_km": 1.82,
        "downhill_flow_conformance_pct": 58.7,
        "copernicus_vs_srtm_elevation_correlation": 0.857,
        "drainage_checks_passed": "15/15",
        "confusion_matrix": {
            "true_positives": 7,
            "false_positives": 2,
            "false_negatives": 1,
            "true_negatives": 18,
            "precision": 0.778,
            "recall_sensitivity": 0.875,
            "specificity": 0.900,
            "f1_score": 0.824,
        },
    }

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'backend', 'data', 'drainiq.db')}"
    )

    def clean_database_url(self) -> str:
        """Normalizes sqlite driver URLs for SQLAlchemy."""
        url = self.DATABASE_URL
        if url.startswith("sqlite:///") or url.startswith("sqlite:////"):
            return url
        if url.startswith("sqlite://"):
            return "sqlite:///" + url[len("sqlite://") :]
        return url

    # Firebase Authentication Settings
    FIREBASE_PROJECT_ID: str = os.getenv("FIREBASE_PROJECT_ID", "drainiq-emergency")
    FIREBASE_CREDENTIALS_PATH: Optional[str] = os.getenv("FIREBASE_CREDENTIALS_PATH", None)
    FIREBASE_AUTH_DISABLED_FOR_DEV: bool = (
        os.getenv("FIREBASE_AUTH_DISABLED_FOR_DEV", "true").lower() in ("true", "1")
    )

    # Legacy JWT settings (used for fallback or internal tokens)
    SECRET_KEY: str = os.getenv("SECRET_KEY", "drainiq-insecure-dev-key-change-in-prod-1234567890")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    DEFAULT_RATE_LIMIT: str = "120/minute"


settings = Settings()
