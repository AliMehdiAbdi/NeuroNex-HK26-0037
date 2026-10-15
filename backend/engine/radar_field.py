"""Spatial rainfall grid sampling and radar precipitation field modeling.

Holds a georeferenced rainfall-intensity grid in mm/hr (typically decoded from weather
radar products) and samples it onto the drainage network's manholes, enabling the engine
to run with a per-node rainfall vector instead of one zone-wide scalar.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import numpy as np


@dataclass
class FieldSample:
    """A rainfall field sampled onto a set of node coordinates."""

    values_mmhr: np.ndarray   # per-node intensity, aligned to the requested points
    covered_mask: np.ndarray  # True where the node lies inside the grid footprint
    covered_mean_mmhr: float  # area-weighted mean over the covered nodes
    fill_value_mmhr: float    # value assigned to uncovered nodes
    coverage_fraction: float  # share of nodes inside the grid footprint

    def summary(self) -> Dict[str, Any]:
        """JSON-safe summary for API responses, logs and verification output."""
        if self.covered_mask.any():
            covered = self.values_mmhr[self.covered_mask]
        else:
            covered = np.zeros(1, dtype=float)
        return {
            "node_count": int(self.values_mmhr.size),
            "coverage_fraction": round(float(self.coverage_fraction), 4),
            "uncovered_node_count": int((~self.covered_mask).sum()),
            "fill_value_mmhr": round(float(self.fill_value_mmhr), 2),
            "covered_mean_mmhr": round(float(self.covered_mean_mmhr), 2),
            "covered_min_mmhr": round(float(covered.min()), 2),
            "covered_max_mmhr": round(float(covered.max()), 2),
        }


@dataclass
class RainfallField:
    """
    A georeferenced rainfall-intensity grid in mm/hr.

    Convention: `values_mmhr[0, 0]` is the north-west cell, and `west`/`south`/`east`/`north`
    are the OUTER EDGES of the outermost cells (not cell centres). Longitude increases
    eastward, latitude increases northward.
    """

    values_mmhr: np.ndarray
    west: float
    south: float
    east: float
    north: float
    frame_time_utc: datetime
    source: str
    station: Optional[str] = None
    crs: str = "EPSG:4326"
    nodata_cells: int = 0

    def __post_init__(self) -> None:
        values = np.asarray(self.values_mmhr, dtype=float)
        if values.ndim != 2:
            raise ValueError(f"values_mmhr must be 2-D (rows, cols), got shape {values.shape}")
        if values.size == 0:
            raise ValueError("values_mmhr is empty")
        if not np.all(np.isfinite(values)):
            raise ValueError(
                "values_mmhr contains non-finite values; map product nodata to 0 mm/hr "
                "(no echo) before constructing a RainfallField"
            )
        if np.any(values < 0.0):
            raise ValueError("values_mmhr contains negative intensities")
        if not (self.east > self.west and self.north > self.south):
            raise ValueError("bounds must satisfy east > west and north > south")
        self.values_mmhr = values

    @classmethod
    def from_grid(
        cls,
        values_mmhr: np.ndarray,
        bounds: Tuple[float, float, float, float],
        frame_time_utc: datetime,
        source: str,
        station: Optional[str] = None,
        crs: str = "EPSG:4326",
        nodata: Optional[float] = None,
    ) -> "RainfallField":
        """
        Builds a field from a decoder grid, mapping nodata cells to 0 mm/hr.

        That mapping is a product convention (radar "no echo" == no rain at that cell), not a
        measurement. The count of such cells is retained in `nodata_cells` and surfaced by
        `to_dict()`, so it can never hide inside a mean.
        """
        values = np.asarray(values_mmhr, dtype=float)
        nodata_cells = 0
        if nodata is not None:
            if np.isnan(nodata):
                mask = np.isnan(values)
            else:
                mask = values == nodata
            nodata_cells = int(mask.sum())
            values = np.where(mask, 0.0, values)
        elif np.isnan(values).any():
            nodata_cells = int(np.isnan(values).sum())
            values = np.nan_to_num(values, nan=0.0)

        west, south, east, north = bounds
        return cls(
            values_mmhr=values,
            west=float(west),
            south=float(south),
            east=float(east),
            north=float(north),
            frame_time_utc=frame_time_utc,
            source=source,
            station=station,
            crs=crs,
            nodata_cells=nodata_cells,
        )

    # --- geometry ------------------------------------------------------------------

    @property
    def shape(self) -> Tuple[int, int]:
        return self.values_mmhr.shape

    @property
    def cell_width_deg(self) -> float:
        return (self.east - self.west) / self.shape[1]

    @property
    def cell_height_deg(self) -> float:
        return (self.north - self.south) / self.shape[0]

    def bounds(self) -> Tuple[float, float, float, float]:
        return (self.west, self.south, self.east, self.north)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe metadata block for API responses and exported artefacts."""
        frame_time = self.frame_time_utc
        if frame_time.tzinfo is None:
            frame_time = frame_time.replace(tzinfo=timezone.utc)
        return {
            "source": self.source,
            "station": self.station,
            "frame_time_utc": frame_time.astimezone(timezone.utc).isoformat(),
            "crs": self.crs,
            "bounds": [self.west, self.south, self.east, self.north],
            "grid_shape": [int(self.shape[0]), int(self.shape[1])],
            "cell_size_deg": [self.cell_width_deg, self.cell_height_deg],
            "nodata_cells": int(self.nodata_cells),
            "field_mean_mmhr": round(float(self.values_mmhr.mean()), 2),
            "field_max_mmhr": round(float(self.values_mmhr.max()), 2),
        }

    # --- sampling ------------------------------------------------------------------

    def sample_to_nodes(
        self,
        lons: np.ndarray,
        lats: np.ndarray,
        weights: Optional[np.ndarray] = None,
    ) -> FieldSample:
        """
        Bilinearly samples the grid at node coordinates given in WGS84 degrees.

        Nodes outside the grid footprint are marked uncovered and assigned the area-weighted
        mean of the covered nodes. That is a documented extrapolation, not a measurement: it
        avoids reporting a false "dry" value at nodes where the radar simply has no coverage,
        and the coverage fraction is always reported alongside the values.

        `weights` (e.g. node catchment areas) are used only for that mean.
        """
        lons_arr = np.asarray(lons, dtype=float).reshape(-1)
        lats_arr = np.asarray(lats, dtype=float).reshape(-1)
        if lons_arr.shape != lats_arr.shape:
            raise ValueError("lons and lats must have the same shape")

        n_points = lons_arr.size
        if n_points == 0:
            raise ValueError("no node coordinates supplied")

        n_rows, n_cols = self.shape

        # Fractional cell coordinates; cell centres sit at integer positions.
        fx = (lons_arr - self.west) / (self.east - self.west) * n_cols - 0.5
        fy = (self.north - lats_arr) / (self.north - self.south) * n_rows - 0.5

        # Coverage is decided on the footprint bounds rather than on the fractional indices,
        # so a node sitting exactly on an edge is not mis-flagged by floating-point error.
        tol = 1e-9
        covered = (
            (lons_arr >= self.west - tol) & (lons_arr <= self.east + tol)
            & (lats_arr >= self.south - tol) & (lats_arr <= self.north + tol)
        )

        if not covered.any():
            raise ValueError("rainfall field does not overlap any node coordinate")

        x = np.clip(fx, 0.0, float(n_cols - 1))
        y = np.clip(fy, 0.0, float(n_rows - 1))
        x0 = np.clip(np.floor(x).astype(int), 0, max(n_cols - 2, 0))
        y0 = np.clip(np.floor(y).astype(int), 0, max(n_rows - 2, 0))
        x1 = np.minimum(x0 + 1, n_cols - 1)
        y1 = np.minimum(y0 + 1, n_rows - 1)
        wx = x - x0
        wy = y - y0

        sampled = (
            self.values_mmhr[y0, x0] * (1.0 - wx) * (1.0 - wy)
            + self.values_mmhr[y0, x1] * wx * (1.0 - wy)
            + self.values_mmhr[y1, x0] * (1.0 - wx) * wy
            + self.values_mmhr[y1, x1] * wx * wy
        )

        if weights is not None:
            weight_arr = np.asarray(weights, dtype=float).reshape(-1)
            if weight_arr.shape[0] != n_points:
                raise ValueError(
                    f"weights must have one entry per node ({n_points}), got {weight_arr.shape[0]}"
                )
            covered_weights = np.clip(weight_arr[covered], 0.0, None)
            if covered_weights.sum() > 0.0:
                fill_value = float((sampled[covered] * covered_weights).sum() / covered_weights.sum())
            else:
                fill_value = float(sampled[covered].mean())
        else:
            fill_value = float(sampled[covered].mean())

        values = np.where(covered, sampled, fill_value)

        return FieldSample(
            values_mmhr=values,
            covered_mask=covered,
            covered_mean_mmhr=fill_value,
            fill_value_mmhr=fill_value,
            coverage_fraction=float(covered.mean()),
        )
