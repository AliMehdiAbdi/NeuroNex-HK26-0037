"""On-demand dynamic rainfall simulations and scientific calibration endpoints."""

import threading
import time
from typing import Any, Dict
from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from backend.app.config import settings
from backend.app.schemas.simulation import (
    CalibrationMetricsResponse,
    CustomSimulateRequest,
    CustomSimulateResponse,
)
from backend.app.services.file_index import file_index

router = APIRouter(prefix="/simulation", tags=["Simulation & Calibration"])

_sim_engines: Dict[str, Any] = {}
_engine_lock = threading.Lock()


def _get_engine(zone: str, runoff_c: float, siltation_factor: float):
    from backend.engine.coupling_engine import HydraulicCouplingEngine

    z_clean = file_index.validate_zone(zone)
    key = f"{z_clean}_{runoff_c}_{siltation_factor}"
    with _engine_lock:
        if key not in _sim_engines:
            _sim_engines[key] = HydraulicCouplingEngine(
                zone=z_clean,
                runoff_c=runoff_c,
                siltation_factor=siltation_factor,
            )
        return _sim_engines[key]


@router.post("/simulate", response_model=CustomSimulateResponse, summary="Run On-Demand Rainfall Simulation")
async def run_custom_simulation(request: CustomSimulateRequest) -> CustomSimulateResponse:
    """Execute live hydraulic simulation for custom rainfall intensity, duration, and siltation."""
    t0 = time.time()
    try:
        def _execute_sim():
            engine = _get_engine(
                zone=request.zone,
                runoff_c=request.runoff_c,
                siltation_factor=request.siltation_factor,
            )

            with _engine_lock:
                scenario_id = f"CUSTOM-{int(request.rainfall_intensity_mmhr)}MM-{time.time_ns()}"
                scenario = engine.rainfall_model.create_uniform_scenario(
                    intensity_mmhr=request.rainfall_intensity_mmhr,
                    duration_hours=request.duration_hours,
                    name=f"Custom {request.rainfall_intensity_mmhr:.0f} mm/hr Simulation",
                    scenario_id=scenario_id,
                )
                time_steps = request.time_steps_min or settings.STANDARD_TIMESTEPS_MIN
                try:
                    return engine.simulate_nowcast(scenario.id, time_steps_min=time_steps)
                finally:
                    engine.rainfall_model.scenarios.pop(scenario_id, None)

        nowcast_dict = await run_in_threadpool(_execute_sim)

        timeline_list = []
        for t_min, res in nowcast_dict.items():
            timeline_list.append({
                "time_minutes": t_min,
                "rainfall_intensity_mmhr": round(res.rainfall_intensity_mmhr, 1),
                "max_depth_cm": res.max_depth_cm,
                "mean_depth_cm": res.mean_depth_cm,
                "overloaded_nodes_count": res.overloaded_nodes_count,
                "overloaded_nodes_pct": round(res.overloaded_nodes_pct, 1),
                "flooded_edges_count": res.flooded_edges_count,
                "impassable_road_length_km": res.impassable_road_length_km,
                "zone_composite_frs": res.zone_composite_frs,
                "zone_risk_category": res.zone_risk_category,
                "total_rain_volume_m3": round(res.total_rain_volume_m3, 1),
                "total_runoff_volume_m3": round(res.total_runoff_volume_m3, 1),
                "total_discharged_volume_m3": round(res.total_discharged_volume_m3, 1),
                "total_surface_excess_volume_m3": round(res.total_surface_excess_volume_m3, 1),
            })

        peak_step = max(timeline_list, key=lambda x: x["max_depth_cm"])
        elapsed_s = time.time() - t0

        return CustomSimulateResponse(
            zone=request.zone,
            rainfall_intensity_mmhr=request.rainfall_intensity_mmhr,
            duration_hours=request.duration_hours,
            simulation_time_s=round(elapsed_s, 3),
            peak_depth_cm=peak_step["max_depth_cm"],
            peak_overloaded_pct=peak_step["overloaded_nodes_pct"],
            timeline=timeline_list,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Simulation execution error: {str(exc)}",
        )


@router.get("/metrics", response_model=CalibrationMetricsResponse, summary="Get Ground-Truth Calibration Benchmarks")
async def get_calibration_metrics() -> CalibrationMetricsResponse:
    """Return Copernicus DEM correlation, proximity tests, and engineering accuracy benchmarks."""
    return CalibrationMetricsResponse(**settings.CALIBRATION_BENCHMARKS)
