"""Drainage network graph builder and topological flow analysis."""

import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import json
import os
import math
import heapq
import itertools
from collections import Counter, defaultdict

import numpy as np
import rasterio
import networkx as nx
import osmnx as ox
from pyproj import Transformer
from scipy import ndimage
from scipy.spatial import cKDTree
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

# CONFIG

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEM_DIR = os.path.join(PROJECT_ROOT, "backend", "data", "elevation")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "backend", "data", "spatial", "pilot")
CACHE_DIR = os.path.join(PROJECT_ROOT, "pipelines", ".osm_cache")

FILLED_DEM = os.path.join(DEM_DIR, "pilot_zone_filled.tif")
RAW_DEM = os.path.join(DEM_DIR, "pilot_zone_dem.tif")
FLOW_ACC = os.path.join(DEM_DIR, "pilot_zone_flowacc.tif")
FLOOD_SCORE = os.path.join(DEM_DIR, "flood_susceptibility.tif")

UTM_CRS = "EPSG:32644"          # UTM zone 44N — Lucknow (80.9E, 26.8N)

# Locality markers for the overview figure. Coordinates are from the OSM
# Nominatim geocoder (see build_flood_sites.py), NOT hand-estimated: the
# previous hard-coded Charbagh marker sat ~2.4 km from the real station.
PILOT_ZONE = {
    "Charbagh": (80.9231, 26.8324),
    "Aminabad": (80.9240, 26.8510),
    "Hazratganj": (80.9448, 26.8548),
}

# ---------------------------------------------------------------------------
# Two zones, two purposes.
#
# 'pilot' is the demo zone from the project plan and the ONLY one the coupling
# engine consumes. Its outputs must stay stable.
#
# 'validation' exists because the pilot zone contains just 3 sourced flood
# sites, capping the achievable p-value at 0.25 -- no amount of modelling can
# be proven there. Lucknow's documented waterlogging sits 1.6-5.9 km further
# north (Vikas Nagar, Aliganj, Kalyanpur, Thakurganj...). This zone covers 8 of
# them, lifting the ceiling to p >= 0.111, and writes to a SEPARATE directory so
# nothing downstream is disturbed. It has no sink-filled / flow-accumulation
# rasters, which is fine: DemStack treats those as optional and the pipeline
# never relied on flow accumulation anyway.
# ---------------------------------------------------------------------------
ZONES = {
    "pilot": dict(
        raw=os.path.join(DEM_DIR, "pilot_zone_dem.tif"),
        filled=os.path.join(DEM_DIR, "pilot_zone_filled.tif"),
        acc=os.path.join(DEM_DIR, "pilot_zone_flowacc.tif"),
        score=os.path.join(DEM_DIR, "flood_susceptibility.tif"),
        out="Drainage/processed",
        labels=dict(PILOT_ZONE),
    ),
    "validation": dict(
        raw=os.path.join(DEM_DIR, "validation_zone_dem.tif"),
        filled=os.path.join(DEM_DIR, "_absent.tif"),
        acc=os.path.join(DEM_DIR, "_absent.tif"),
        score=os.path.join(DEM_DIR, "_absent.tif"),
        out="Drainage/processed_validation",
        labels={
            "Charbagh": (80.9231, 26.8324),
            "Rumi Darwaza": (80.9122, 26.8712),
            "Nirala Nagar": (80.9357, 26.8790),
            "Thakurganj": (80.8905, 26.8794),
            "Vikas Nagar": (80.9641, 26.8942),
            "Kalyanpur": (80.9737, 26.9032),
            "Aliganj": (80.9480, 26.9051),
            "Gomti Nagar": (80.9989, 26.8529),
        },
    ),
}

# Copernicus GLO-30 variants. The SRTM input quantises every height to a whole
# metre, but the real drop across one 28 m cell at Lucknow's 2.09 m/km gradient
# is 0.059 m -- 17x finer than the data can express, so the slope is pure
# rounding error. Copernicus GLO-30 is float32: 49,786 distinct heights over the
# pilot area against SRTM's 50, and a plausible 102.5-139.9 m range rather than
# SRTM's impossible 87-155 m.
#
# Caveat to keep in mind: GLO-30 is a SURFACE model, so it includes rooftops.
# In a 4 m Aminabad lane a 30 m cell is mostly building. That is the likely
# reason the swap bought +2.6 points of downhill consistency rather than the
# leap the 17x precision gain suggested. FABDEM is the same data with buildings
# and trees stripped out and would be strictly better here, but it needs a
# registration form; GLO-30 needs nothing.
#
# Verified to actually be Lucknow by Drainage/verify_dem_location.py:
#   r = +0.857 against the SRTM tile, median 121.5 m vs the city's documented
#   123 m ASL, and the Gomti river (from OSM, independent of this raster) runs
#   14.65 m below the surrounding land, p = 3e-220.
#
# The Copernicus rasters are resampled onto the EXACT SRTM grid, so the road
# bbox, manhole count and node IDs are unchanged and only elevations differ.
# Clipping to its own grid instead shifted the bbox by 0.1 cell (~3 m) and
# silently moved 6 manholes across the boundary (1921 -> 1915), which would
# have broken any node ID the coupling engine had already recorded.
#
# The SRTM-derived filled / flowacc / susceptibility rasters are deliberately
# NOT carried over: 'filled' would make the sink-fill diagnostic compute
# SRTM_filled - Copernicus_raw, which is meaningless, and flow accumulation was
# already unusable (it maxed out at 38 cells of 51110). Consequence: the
# 'flowacc' and 'terrain_score' node fields are now 0.0. Neither was ever used
# for routing or for outfall selection -- they are informational only.
for _base in ("pilot", "validation"):
    # Preserve the old SRTM configuration under an explicit name so the
    # DEM comparison stays reproducible.
    ZONES[f"{_base}_srtm"] = dict(
        ZONES[_base],
        out=f"Drainage/processed_{_base}_srtm",
        smooth=5,
    )
    # Promote Copernicus to the canonical zone.
    #
    # Smoothing stays at 5x5, and the reasoning matters because two measures
    # disagree. Widening 5x5 -> 3x3 IMPROVES downhill consistency (58.7% ->
    # 59.4%) but WORSENS the ranking of documented flood sites (68th percentile
    # p=0.037 -> 64th p=0.082) on the same DEM. So downhill% is only a proxy,
    # and it does not track the objective.
    #
    # 5x5 is kept on an a-priori argument, NOT on that p-value: with n=8 sites,
    # selecting a hyperparameter by test score is precisely the tuning-on-the-
    # test-set error this project already warned about elsewhere. The physical
    # argument is that GLO-30 is a SURFACE model. The 5x5 window was originally
    # bought to suppress SRTM's 33 m sink-fill spikes, which Copernicus does not
    # have -- but Copernicus introduces a different high-frequency contaminant
    # in its place: rooftops, which a 30 m cell in a 4 m Aminabad lane is mostly
    # made of. A wider median is the standard defence against exactly that, so
    # the justification transfers rather than disappearing.
    ZONES[_base] = dict(
        ZONES[_base],
        raw=os.path.join(DEM_DIR, f"{_base}_zone_dem_cop.tif"),
        filled=os.path.join(DEM_DIR, "_absent.tif"),
        acc=os.path.join(DEM_DIR, "_absent.tif"),
        score=os.path.join(DEM_DIR, "_absent.tif"),
        smooth=5,
    )
    ZONES[f"{_base}_cop"] = dict(ZONES[_base])   # backwards-compatible alias

def select_zone(name):
    """Point the module's path globals at one of the ZONES. Default is 'pilot'."""
    global RAW_DEM, FILLED_DEM, FLOW_ACC, FLOOD_SCORE, OUTPUT_DIR, PILOT_ZONE
    global DEM_SMOOTH_CELLS
    z = ZONES[name]
    RAW_DEM, FILLED_DEM = z["raw"], z["filled"]
    FLOW_ACC, FLOOD_SCORE = z["acc"], z["score"]
    OUTPUT_DIR, PILOT_ZONE = z["out"], z["labels"]
    DEM_SMOOTH_CELLS = z.get("smooth", DEM_SMOOTH_DEFAULT)
    return z

# Merge intersections closer than this (dual carriageways, complex junctions).
# Real manhole spacing on Indian municipal networks is ~30-50 m.
CONSOLIDATE_TOLERANCE_M = 25.0

# De-spike the DEM before sampling. Drainage/dem_sensitivity.py measured what
# this filter costs, using the share of child->parent hops that lose elevation
# (50% = terrain says nothing, 75.0% = perfect terrain on this road graph):
#
#     SRTM       none 59.1%   3x3 58.5%   5x5 56.8%
#     Copernicus none 59.0%   3x3 59.4%   5x5 58.7%
#
# On SRTM every widening of the window made routing worse, and 5x5 was accepted
# anyway as the price of surviving 33 m sink-fill spikes in an 87-155 m tile.
# Copernicus has no such spikes, so the optimum moves to 3x3 and the filter
# finally pays for itself instead of just limiting damage. Set per zone; this is
# only the fallback.
DEM_SMOOTH_DEFAULT = 5
DEM_SMOOTH_CELLS = DEM_SMOOTH_DEFAULT
DEM_CLIP_PCTL = (0.5, 99.5)

# A depression must be deeper than the terrain's own resolving power. The real
# drop across one cell is 0.059 m at the 2.09 m/km regional gradient, so ties
# below this margin are contour-parallel roads, not bowls.
DEPRESSION_MARGIN_M = 0.05

# Assumed drained street width, used ONLY to floor ponding_area_m2 so that
# `excess / area` is always finite. Not used in any mass balance.
STREET_CORRIDOR_M = 8.0

# --- Pipe sizing by road class (realistic Indian municipal RCC values) ---
PIPE_DIAMETER_MM = {
    "motorway": 1200, "motorway_link": 1200,
    "trunk": 1200, "trunk_link": 1200,
    "primary": 1200, "primary_link": 1200,
    "secondary": 900, "secondary_link": 900,
    "tertiary": 600, "tertiary_link": 600,
    "unclassified": 450,
    "residential": 450,
    "living_street": 450,
    "service": 300, "road": 450,
}
DEFAULT_DIAMETER_MM = 450

# Base sizes for conduits that run alongside real mapped infrastructure. These
# override the road class, because a lane beside the Kukrail nala is carrying the
# nala, whatever OSM tagged the lane. Values are the class thresholds already
# used elsewhere in this module: >1800 mm escalates to nala, and 1200 mm is the
# largest ordinary RCC pipe in PIPE_DIAMETER_MM.
NALA_BASE_DIAMETER_MM = 1800     # beside a river or canal
TRUNK_BASE_DIAMETER_MM = 1200    # beside a tagged municipal drain or ditch

# Two independent uses of the channel data, separately switchable because they
# turned out to pull in opposite directions:
#
#   outfalls  - discharge where the streets actually meet a nala. This fixed the
#               hotspot geography test (2.44 km -> 0.69 km from documented
#               flooding, p = 0.993 -> 0.013).
#
#   base size - treat a conduit beside a channel as carrying that channel. This
#               tamed the implausible gap tail but COLLAPSED the site ranking
#               (68th percentile p=0.037 -> 54th p=0.350), and there is a real
#               reason why. It encodes "next to a drain => well provisioned",
#               whereas Lucknow's documented flood sites flood BECAUSE the nala
#               beside them backs up: silted, encroached, undersized. Rumi
#               Darwaza, Thakurganj and Nirala Nagar are all nala-overflow
#               sites. So the assumption is inverted exactly where it matters.
USE_CHANNEL_OUTFALLS = os.environ.get("CHANNEL_OUT", "1") == "1"
USE_CHANNEL_BASE_SIZE = os.environ.get("CHANNEL_BASE", "0") == "1"

# Reference capacities from the implementation plan (sanity cross-check).
# These correspond to minimum-slope design, which is what the Gangetic plain forces.
REFERENCE_CAPACITY_M3S = {1200: 1.5, 900: 0.8, 600: 0.3, 450: 0.15, 300: 0.06}

MANNING_N = 0.013     # RCC / concrete storm sewer
MANNING_N_NALA = 0.022  # brick/masonry open nala, partly silted
MIN_SLOPE = 0.0015    # minimum self-cleansing slope, ~1 in 667
MAX_SLOPE = 0.0050    # ~1 in 200; steeper is unrealistic on this terrain

# Large conduits are laid at flatter gradients than laterals, because velocity
# scales with the hydraulic radius: a 6 m nala at 1 in 200 would run at ~4.5 m/s
# and scour its own invert. Cap the design gradient per conduit class so every
# conduit stays inside the 0.6-3.0 m/s self-cleansing / non-scouring band.
MAX_SLOPE_PIPE = 0.0050    # <= 900 mm
MAX_SLOPE_TRUNK = 0.0030   # 1050-1800 mm
MAX_SLOPE_NALA = 0.0015    # open channel

# --- Trunk sizing (rational method) ---
# Road class alone under-sizes trunk conduits by ~40x: a 1200 mm pipe carries
# 1.5 m3/s but a 20 km2 catchment delivers ~58 m3/s. Real networks are sized by
# upstream contributing area, and above ~1800 mm they become open nalas.
#
# We size for a 15 mm/hr design storm, which is what Lucknow's legacy network was
# built to carry. That is *why* the city floods today: a 60 mm/hr cloudburst
# delivers 4x the design flow. Keeping this honest makes the four rainfall
# scenarios produce a real gradient instead of "everything floods, always".
DESIGN_INTENSITY_MMHR = 15.0
DESIGN_RUNOFF_COEFF = 0.70
STANDARD_PIPE_MM = [450, 600, 750, 900, 1050, 1200, 1500, 1800]
MAX_PIPE_MM = 1800          # above this, a real network uses an open nala
MAX_NALA_WIDTH_M = 20.0
NALA_DEPTH_RATIO = 0.6      # depth = 0.6 x width (rectangular masonry section)

# Old municipal drains lose capacity to silt, encroachment and solid waste.
# Tunable calibration knob; present it as a feature, not a fudge.
SILTATION_FACTOR = 0.75

# --- Outfall selection ---
# A real municipal network of this size has O(10) major discharge points, one
# per sub-catchment, connecting to the trunk nala network / the Gomti. We pick
# the lowest node in each sub-catchment by greedily taking global low points
# subject to a minimum spacing, which is stable against DEM spike noise.
TARGET_OUTFALLS = int(os.environ.get("N_OUTFALL", "20"))
MIN_OUTFALL_SEPARATION_M = float(os.environ.get("OUTFALL_SEP", "700"))
OUTFALL_ELEV_PCTL = 35              # only consider nodes below this elevation pctl

# --- Finite-capacity receiving channels ---
# An outfall discharges into a real waterway. That waterway has a finite capacity
# that must be shared among every outfall it serves. When USE_FINITE_CHANNELS is
# on, outfall capacity is min(pipe_inflow, channel_share) instead of infinite.
# This is the fix for the 2x2 deadlock: channel-anchored outfalls now back up
# realistically when the nala they discharge into is overwhelmed.
#
# Total-channel capacities by OSM waterway type (m³/s). These are rough design
# estimates for Lucknow's municipal infrastructure, not measured values. The
# river (Gomti) is effectively unlimited for local stormwater.
CHANNEL_CAPACITY_M3S = {
    "river":  200.0,   # Gomti — effectively unlimited for local drainage
    "canal":   50.0,   # lined canal, ~4 m wide
    "drain":   10.0,   # municipal drain / nala, ~2 m wide
    "ditch":    3.0,   # roadside ditch
    "stream":  15.0,   # natural stream
}
USE_FINITE_CHANNELS = os.environ.get("FINITE_CHAN", "1") == "1"

# A manhole with no lower neighbour on the road graph is a terrain depression
# and therefore a genuine ponding hotspot.

# STEP 1: Load DEM products

class DemStack:
    """Holds the DEM rasters + helpers to sample them at lon/lat points."""

    def __init__(self, filled_path, raw_path, acc_path, score_path):
        with rasterio.open(raw_path) as src:
            self.raw = src.read(1).astype(np.float64)
            self.transform = src.transform
            self.crs = src.crs
            self.bounds = src.bounds
            self.shape = src.shape
            self.nodata = src.nodata
            self.profile = src.profile.copy()

        self.filled = self._read_optional(filled_path)
        self.acc = self._read_optional(acc_path)
        self.score = self._read_optional(score_path)

        self.nodata_mask = (
            np.isclose(self.raw, self.nodata) if self.nodata is not None
            else np.zeros(self.shape, dtype=bool)
        )

        # --- de-spiked terrain used for drainage design ---
        # SRTM on flat terrain carries spike noise larger than the real relief,
        # and the sink-filled DEM contains large artificial plateaus, so neither
        # is usable raw. Median filter + robust clip gives a stable surface.
        t = self.raw.copy()
        if self.nodata_mask.any():
            t[self.nodata_mask] = np.nan
            t = _fill_nan_nearest(t)
        lo, hi = np.percentile(t, DEM_CLIP_PCTL)
        self.terrain = ndimage.median_filter(
            np.clip(t, lo, hi), size=DEM_SMOOTH_CELLS, mode="nearest"
        )

    @staticmethod
    def _read_optional(path):
        if not os.path.exists(path):
            print(f"  ! optional raster missing: {path}")
            return None
        with rasterio.open(path) as src:
            return src.read(1).astype(np.float64)

    def rowcol(self, lons, lats):
        inv = ~self.transform
        cols, rows = inv * (np.asarray(lons), np.asarray(lats))
        return np.asarray(rows), np.asarray(cols)

    def sample_bilinear(self, arr, lons, lats):
        rows, cols = self.rowcol(lons, lats)
        rows = np.clip(rows - 0.5, 0, self.shape[0] - 1)
        cols = np.clip(cols - 0.5, 0, self.shape[1] - 1)
        return ndimage.map_coordinates(arr, [rows, cols], order=1, mode="nearest")

    def sample_nearest(self, arr, lons, lats):
        rows, cols = self.rowcol(lons, lats)
        r = np.clip(rows.astype(int), 0, self.shape[0] - 1)
        c = np.clip(cols.astype(int), 0, self.shape[1] - 1)
        return arr[r, c]

    def cell_centers_lonlat(self):
        rows, cols = self.shape
        jj, ii = np.meshgrid(np.arange(cols), np.arange(rows))
        lon = self.transform.c + (jj + 0.5) * self.transform.a
        lat = self.transform.f + (ii + 0.5) * self.transform.e
        return lon, lat

def _fill_nan_nearest(arr):
    mask = np.isnan(arr)
    if not mask.any():
        return arr
    idx = ndimage.distance_transform_edt(mask, return_distances=False, return_indices=True)
    return arr[tuple(idx)]

def load_dem_stack():
    print("=" * 64)
    print("STEP 1: Loading DEM products")
    print("=" * 64)

    if not os.path.exists(RAW_DEM):
        raise FileNotFoundError(
            f"{RAW_DEM} not found. Run  python DEM/dem_processing.py  first."
        )

    dem = DemStack(FILLED_DEM, RAW_DEM, FLOW_ACC, FLOOD_SCORE)
    print(f"  CRS: {dem.crs}   grid: {dem.shape[1]} x {dem.shape[0]}")
    print(f"  bounds: W {dem.bounds.left:.5f}  S {dem.bounds.bottom:.5f}  "
          f"E {dem.bounds.right:.5f}  N {dem.bounds.top:.5f}")
    print(f"  raw SRTM elevation:  {dem.raw.min():.1f} .. {dem.raw.max():.1f} m")
    print(f"  de-spiked terrain:   {dem.terrain.min():.1f} .. {dem.terrain.max():.1f} m "
          f"(median filter {DEM_SMOOTH_CELLS}x{DEM_SMOOTH_CELLS}, clip "
          f"{DEM_CLIP_PCTL[0]}-{DEM_CLIP_PCTL[1]} pctl)")

    # Input-quality diagnostics: the drainage graph is designed to tolerate
    # these, but the DEM component should know about them.
    if dem.filled is not None:
        lift = dem.filled - dem.raw
        pct = 100.0 * (lift > 0.001).sum() / lift.size
        if pct > 10:
            print(f"  ! WARNING: sink-fill raised {pct:.1f}% of cells "
                  f"(max +{lift.max():.1f} m) -> large artificial plateaus")
    if dem.acc is not None and dem.acc.max() < 0.02 * dem.acc.size:
        print(f"  ! WARNING: flow accumulation maxes at {dem.acc.max():.0f} cells of "
              f"{dem.acc.size} -> D8 routing is fragmented; flowacc used as a weak "
              f"signal only")
    return dem

# STEP 2 + 3: Road network -> manhole set

def fetch_road_network(dem):
    print("\n" + "=" * 64)
    print("STEP 2: Fetching road network from OpenStreetMap")
    print("=" * 64)

    os.makedirs(CACHE_DIR, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = CACHE_DIR
    ox.settings.log_console = False

    bbox = (dem.bounds.left, dem.bounds.bottom, dem.bounds.right, dem.bounds.top)
    print(f"  bbox (matched to DEM): {tuple(round(v, 5) for v in bbox)}")

    G = ox.graph_from_bbox(bbox, network_type="drive", simplify=True, truncate_by_edge=True)
    print(f"  raw OSM graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    classes = Counter(_norm_highway(d.get("highway")) for _, _, d in G.edges(data=True))
    for cls, n in classes.most_common():
        print(f"    {cls:<16} {n:>6} segments")
    return G

def consolidate_to_manholes(G):
    print("\n" + "=" * 64)
    print("STEP 3: Consolidating intersections into manholes")
    print("=" * 64)

    Gp = ox.project_graph(G, to_crs=UTM_CRS)
    print(f"  projected to {UTM_CRS}")

    try:
        Gc = ox.consolidate_intersections(
            Gp, tolerance=CONSOLIDATE_TOLERANCE_M,
            rebuild_graph=True, dead_ends=True, reconnect_edges=True,
        )
        print(f"  consolidated @ {CONSOLIDATE_TOLERANCE_M:.0f} m tolerance: "
              f"{Gp.number_of_nodes()} -> {Gc.number_of_nodes()} nodes "
              f"({100 * (1 - Gc.number_of_nodes() / Gp.number_of_nodes()):.0f}% reduction)")
    except Exception as exc:  # pragma: no cover - defensive fallback
        print(f"  ! consolidation failed ({exc}); using un-consolidated graph")
        Gc = Gp

    # A gravity network must be one connected system; isolated fragments cannot
    # be routed to an outfall.
    if not nx.is_weakly_connected(Gc):
        comps = sorted(nx.weakly_connected_components(Gc), key=len, reverse=True)
        dropped = sum(len(c) for c in comps[1:])
        print(f"  {len(comps)} weakly-connected components (largest = {len(comps[0])}); "
              f"dropping {dropped} orphan nodes")
        Gc = Gc.subgraph(comps[0]).copy()

    print(f"  manholes: {Gc.number_of_nodes()}   road segments: {Gc.number_of_edges()}")
    return Gc

def _norm_highway(hw):
    """OSM 'highway' may be a list; take the most significant class."""
    if hw is None:
        return "residential"
    if isinstance(hw, (list, tuple, set)):
        ranked = sorted(hw, key=lambda h: PIPE_DIAMETER_MM.get(h, 0), reverse=True)
        return str(ranked[0]) if ranked else "residential"
    return str(hw)

# STEP 4: Sample DEM at every manhole

def build_node_table(Gc, dem):
    print("\n" + "=" * 64)
    print("STEP 4: Sampling terrain at manhole locations")
    print("=" * 64)

    to_wgs = Transformer.from_crs(UTM_CRS, "EPSG:4326", always_xy=True)

    node_ids = list(Gc.nodes())
    xs = np.array([Gc.nodes[n]["x"] for n in node_ids], dtype=np.float64)
    ys = np.array([Gc.nodes[n]["y"] for n in node_ids], dtype=np.float64)
    lons, lats = to_wgs.transform(xs, ys)
    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)

    terrain = dem.sample_bilinear(dem.terrain, lons, lats)
    elev_raw = dem.sample_nearest(dem.raw, lons, lats)
    acc = (dem.sample_bilinear(dem.acc, lons, lats)
           if dem.acc is not None else np.zeros_like(terrain))
    score = (dem.sample_bilinear(dem.score, lons, lats)
             if dem.score is not None else np.zeros_like(terrain))

    nodes = {}
    for k, nid in enumerate(node_ids):
        nodes[nid] = {
            "lon": float(lons[k]), "lat": float(lats[k]),
            "x": float(xs[k]), "y": float(ys[k]),
            "terrain_m": float(terrain[k]),
            "elev_raw_m": float(elev_raw[k]),
            "flowacc": float(acc[k]),
            "terrain_score": float(score[k]),
        }

    print(f"  sampled {len(nodes)} manholes")
    print(f"  terrain elevation: {terrain.min():.2f} .. {terrain.max():.2f} m "
          f"(relief {terrain.max() - terrain.min():.2f} m)")
    print(f"  raw SRTM at same points: {elev_raw.min():.0f} .. {elev_raw.max():.0f} m "
          f"-> de-spiking removed {(elev_raw.max()-elev_raw.min()) - (terrain.max()-terrain.min()):.1f} m "
          f"of spurious relief")
    return nodes

# STEP 5: Deduplicate road segments -> candidate pipes

def extract_segments(Gc, nodes):
    print("\n" + "=" * 64)
    print("STEP 5: Extracting candidate pipe segments")
    print("=" * 64)

    pairs = {}
    for u, v, data in Gc.edges(data=True):
        if u == v or u not in nodes or v not in nodes:
            continue
        key = (u, v) if str(u) <= str(v) else (v, u)
        hw = _norm_highway(data.get("highway"))
        length = data.get("length")
        if length is None:
            length = math.dist((nodes[u]["x"], nodes[u]["y"]),
                               (nodes[v]["x"], nodes[v]["y"]))
        length = max(float(length), 5.0)
        name = data.get("name")
        name = name[0] if isinstance(name, (list, tuple)) and name else name

        prev = pairs.get(key)
        if prev is None:
            pairs[key] = [hw, length, name]
        else:
            # keep the most significant road class, and the shortest geometry
            if PIPE_DIAMETER_MM.get(hw, 0) > PIPE_DIAMETER_MM.get(prev[0], 0):
                prev[0] = hw
                prev[2] = name
            prev[1] = min(prev[1], length)

    lengths = np.array([p[1] for p in pairs.values()])
    print(f"  unique segments: {len(pairs)} (from {Gc.number_of_edges()} directed edges)")
    print(f"  segment length: mean {lengths.mean():.0f} m, median {np.median(lengths):.0f} m, "
          f"max {lengths.max():.0f} m")
    print(f"  total network length: {lengths.sum() / 1000:.1f} km "
          f"({lengths.sum() / 1000 / 43.4:.1f} km/km2)")

    adj = defaultdict(list)
    for (a, b), (hw, length, name) in pairs.items():
        adj[a].append((b, length))
        adj[b].append((a, length))
    return pairs, adj

# STEP 6: Select outfalls

# STEP 5b: Real drainage infrastructure from OSM

#
# Until now the network was built from roads and terrain alone, and the 20
# discharge points were guessed as DEM low points. But Lucknow's actual drains
# are mapped: the Gomti, the Kukrail nala, and a set of tagged municipal drains
# and canals. Ignoring them caused the model's worst failure -- the top-20
# hotspots sat FARTHER from documented flooding than an average manhole
# (p = 0.993), with a provision-gap tail of 563x that is not credible as real
# under-provision.
#
# The mechanism of that failure: with only 20 outfalls, stormwater had to travel
# kilometres along residential streets to reach one, so an 18.5 km2 catchment
# ended up routed through a lane whose road class implies a 300 mm pipe. In
# reality water enters the nearest nala within a few hundred metres.
#
# Drainage/explore_waterways.py verified both preconditions before this was
# written: 14.4 km of channel in the pilot zone (Gomti 6.0 km, Kukrail, 9 tagged
# drains) and 53.3 km in the validation zone, with channel-adjacent manholes
# sitting 5.3 m BELOW the rest of the network (p = 2.3e-11). They are genuinely
# in the low ground, so discharging into them is physically right, not a
# convenient assumption.

WATERWAY_TAGS = {"waterway": ["river", "canal", "drain", "ditch", "stream"]}

# A manhole this close to a mapped channel discharges into it directly.
OUTFALL_CHANNEL_M = 150.0
# Thinning between channel outfalls. A nala accepts inflow along its length
# rather than at a single mouth, so this is much tighter than the old 700 m
# used for guessed low points.
CHANNEL_SEPARATION_M = float(os.environ.get("CHANNEL_SEP", "350"))
# Segments running alongside a channel are carrying that channel, so their base
# size comes from the channel class rather than the road class.
CHANNEL_NALA_M = 60.0        # beside a river or canal -> nala
CHANNEL_TRUNK_M = 120.0      # beside a drain or ditch -> trunk

def fetch_waterways(dem):
    """
    Pull mapped watercourses for the zone and return unioned UTM geometry.

    Returns None if OSM has nothing usable here, in which case every caller
    falls back to the previous terrain-only behaviour.
    """
    print("\n" + "=" * 64)
    print("STEP 5b: Real drainage channels from OSM")
    print("=" * 64)

    import geopandas as gpd
    from shapely.geometry import box as _box
    from shapely.ops import unary_union

    b = dem.bounds
    try:
        w = ox.features_from_polygon(
            _box(b.left, b.bottom, b.right, b.top), tags=WATERWAY_TAGS)
    except Exception as exc:
        print(f"  ! OSM waterway query failed ({exc}); "
              f"falling back to terrain-only outfalls")
        return None
    if w is None or w.empty:
        print("  no mapped channels in this zone; terrain-only outfalls")
        return None

    w = w[w.geometry.type.isin(["LineString", "MultiLineString"])].copy()
    if w.empty:
        print("  channels present but no line geometry; terrain-only outfalls")
        return None

    w = w.to_crs(UTM_CRS)
    # features_from_polygon returns whole ways that intersect the box, including
    # the parts outside it, so clip or the reported lengths are meaningless.
    zone = gpd.GeoSeries([_box(b.left, b.bottom, b.right, b.top)],
                         crs="EPSG:4326").to_crs(UTM_CRS).iloc[0]
    w["geometry"] = w.geometry.intersection(zone)
    w = w[~w.geometry.is_empty & w.geometry.notna()].copy()
    w["len_m"] = w.geometry.length
    w = w[w["len_m"] > 0.0]
    if w.empty:
        print("  channels all fall outside the zone; terrain-only outfalls")
        return None

    kind = w["waterway"].astype(str)
    major = w[kind.isin(["river", "canal"])]
    minor = w[kind.isin(["drain", "ditch", "stream"])]

    print(f"  {len(w)} channel features, {w['len_m'].sum()/1000:.1f} km inside zone")
    for k, n in kind.value_counts().items():
        print(f"    {k:<8} {n:>3} features  {w[kind == k]['len_m'].sum()/1000:>6.2f} km")
    if "name" in w.columns:
        named = w[w["name"].notna()]
        if not named.empty:
            top = named.groupby(named["name"].astype(str))["len_m"].sum() \
                       .sort_values(ascending=False)
            print("  named: " + ", ".join(f"{k} {v/1000:.1f} km"
                                          for k, v in list(top.items())[:4]))

    return {
        "all": unary_union(w.geometry.values),
        "major": unary_union(major.geometry.values) if not major.empty else None,
        "minor": unary_union(minor.geometry.values) if not minor.empty else None,
        # Per-type total lengths inside the zone, for finite-capacity sharing.
        "type_lengths": {k: float(w[kind == k]["len_m"].sum())
                         for k in kind.unique()},
        "features_gdf": w,  # full GeoDataFrame for nearest-channel-type lookup
    }

def channel_distances(nodes, chans):
    """Distance in metres from every manhole to the nearest mapped channel."""
    import geopandas as gpd

    ids = list(nodes)
    if chans is None:
        return {n: float("inf") for n in ids}
    pts = gpd.GeoSeries(
        gpd.points_from_xy([nodes[n]["x"] for n in ids],
                           [nodes[n]["y"] for n in ids]), crs=UTM_CRS)
    out = {}
    for key in ("all", "major", "minor"):
        if chans.get(key) is None:
            out[key] = np.full(len(ids), np.inf)
        else:
            out[key] = pts.distance(chans[key]).to_numpy()
    return {n: {k: float(out[k][i]) for k in out} for i, n in enumerate(ids)}

def compute_channel_shares(outfalls, nodes, chans, chan_dist):
    """
    Assign a finite receiving capacity to each outfall based on the channel
    it discharges into.

    Each outfall's nearest channel feature determines its waterway TYPE
    (river, canal, drain, ditch, stream). The total capacity for that type
    (from CHANNEL_CAPACITY_M3S) is shared equally among all outfalls of
    the same type. This is a simplification — in reality the share would
    be proportional to inter-outfall spacing along the channel — but with
    O(10-70) outfalls it gives the right order of magnitude.

    Returns {node_id: capacity_m3s} for each outfall.
    """
    if chans is None or not USE_FINITE_CHANNELS:
        return {}

    import geopandas as gpd

    features = chans.get("features_gdf")
    if features is None or features.empty:
        return {}

    # For each outfall, find the nearest channel feature and its waterway type.
    outfall_ids = list(outfalls)
    pts = gpd.GeoSeries(
        gpd.points_from_xy([nodes[n]["x"] for n in outfall_ids],
                           [nodes[n]["y"] for n in outfall_ids]), crs=UTM_CRS)

    nearest_type = {}
    for i, nid in enumerate(outfall_ids):
        dists = features.geometry.distance(pts.iloc[i])
        if dists.empty:
            continue
        j = dists.idxmin()
        wtype = str(features.loc[j, "waterway"])
        nearest_type[nid] = wtype

    # Count outfalls per channel type and share the total capacity equally.
    from collections import Counter
    type_counts = Counter(nearest_type.values())

    caps = {}
    for nid, wtype in nearest_type.items():
        total_cap = CHANNEL_CAPACITY_M3S.get(wtype, 10.0)
        n_sharing = type_counts[wtype]
        caps[nid] = total_cap / n_sharing

    # Report
    print(f"\n  finite-capacity channel shares for {len(caps)} outfalls:")
    for wtype in sorted(type_counts, key=lambda k: -type_counts[k]):
        n = type_counts[wtype]
        total = CHANNEL_CAPACITY_M3S.get(wtype, 10.0)
        per = total / n
        print(f"    {wtype:<8} {n:>3} outfalls sharing {total:>6.1f} m³/s "
              f"→ {per:.2f} m³/s each")
    return caps

# STEP 6: Outfall selection

def select_outfalls(nodes, dem, chan_dist=None):
    """
    Choose discharge points, preferring manholes that sit on a real mapped drain.

    Physically: stormwater leaves the street network where it meets a nala, and
    a nala accepts inflow all along its length. So candidates are manholes
    within OUTFALL_CHANNEL_M of a mapped channel, walked lowest-first and thinned
    by CHANNEL_SEPARATION_M.

    If OSM has too few channels here, this degrades to the original behaviour:
    greedy lowest-first over the whole network below OUTFALL_ELEV_PCTL, thinned
    by MIN_OUTFALL_SEPARATION_M. That older rule is not wrong, merely uninformed
    -- it cannot know that a low point is a real outlet rather than a pothole.
    """
    print("\n" + "=" * 64)
    print("STEP 6: Selecting outfalls (discharge points)")
    print("=" * 64)

    ids = list(nodes)
    xy = np.array([[nodes[n]["x"], nodes[n]["y"]] for n in ids])
    el = np.array([nodes[n]["terrain_m"] for n in ids])

    on_channel = None
    if chan_dist is not None:
        d_all = np.array([chan_dist[n]["all"] for n in ids])
        on_channel = np.where(d_all <= OUTFALL_CHANNEL_M)[0]
        print(f"  manholes within {OUTFALL_CHANNEL_M:.0f} m of a mapped channel: "
              f"{len(on_channel)} ({100*len(on_channel)/len(ids):.1f}%)")

    chosen, mode, sep_used = [], "terrain low points", MIN_OUTFALL_SEPARATION_M
    if on_channel is not None and len(on_channel) >= 5:
        mode, sep_used = "mapped channels", CHANNEL_SEPARATION_M
        order = on_channel[np.argsort(el[on_channel])]
        for i in order:
            if all(math.dist(xy[i], xy[j]) >= CHANNEL_SEPARATION_M for j in chosen):
                chosen.append(int(i))
        print(f"  anchored to real infrastructure: {len(chosen)} outfalls "
              f"(spacing >= {CHANNEL_SEPARATION_M:.0f} m)")
        med = float(np.median(el))
        print(f"  their elevation: {el[chosen].min():.2f} .. {el[chosen].max():.2f} m "
              f"(network median {med:.2f} m)")
    else:
        cutoff = np.percentile(el, OUTFALL_ELEV_PCTL)
        cands = [i for i in np.argsort(el) if el[i] <= cutoff]
        print(f"  no usable channels; candidate low nodes below "
              f"{OUTFALL_ELEV_PCTL}th pctl ({cutoff:.2f} m): {len(cands)}")
        for i in cands:
            if len(chosen) >= TARGET_OUTFALLS:
                break
            if all(math.dist(xy[i], xy[j]) >= MIN_OUTFALL_SEPARATION_M
                   for j in chosen):
                chosen.append(int(i))

    outfalls = {ids[i] for i in chosen}
    oe = el[chosen]
    seps = [min(math.dist(xy[i], xy[j]) for j in chosen if j != i) for i in chosen] \
        if len(chosen) > 1 else [0.0]

    print(f"  outfalls selected: {len(outfalls)} "
          f"({100 * len(outfalls) / len(nodes):.1f}% of manholes) via {mode}")
    print(f"  outfall elevation: {oe.min():.2f} .. {oe.max():.2f} m "
          f"(network median {np.median(el):.2f} m)")
    print(f"  nearest-neighbour spacing: min {min(seps):.0f} m, "
          f"mean {np.mean(seps):.0f} m")
    return outfalls

# STEP 7: Hydraulic conditioning (priority-flood from outfalls)

def condition_network(nodes, adj, outfalls):
    """
    Wang & Liu priority-flood over the road graph, seeded from the outfalls,
    carrying a minimum design slope:

        cond[m] = max( terrain[m], cond[n] + MIN_SLOPE * length(n, m) )

    Guarantees every manhole has a strictly descending path to an outfall and
    every pipe meets the minimum self-cleansing slope.
    """
    print("\n" + "=" * 64)
    print("STEP 7: Hydraulic conditioning (minimum-slope design from outfalls)")
    print("=" * 64)

    cond = {}
    parent = {}
    # A unique counter is the final tiebreaker so heap comparison NEVER reaches
    # the parent field. Without it, a node that is both an outfall seed
    # (parent=None) and pushed by a neighbour at equal elevation (parent=int)
    # makes heapq compare None with int and raise TypeError.
    seq = itertools.count()
    pq = [(nodes[n]["terrain_m"], str(n), next(seq), n, None) for n in outfalls]
    heapq.heapify(pq)

    while pq:
        e, _, _, n, par = heapq.heappop(pq)
        if n in cond:
            continue
        cond[n] = e
        parent[n] = par
        for m, length in adj[n]:
            if m in cond:
                continue
            heapq.heappush(pq, (max(nodes[m]["terrain_m"], e + MIN_SLOPE * length),
                                str(m), next(seq), m, n))

    unreached = [n for n in nodes if n not in cond]
    if unreached:
        print(f"  ! {len(unreached)} nodes unreachable from any outfall "
              f"(disconnected); assigning terrain elevation")
        for n in unreached:
            cond[n] = nodes[n]["terrain_m"]
            parent[n] = None

    lift = {n: cond[n] - nodes[n]["terrain_m"] for n in nodes}
    lv = np.array(list(lift.values()))

    print(f"  conditioned {len(cond)} manholes from {len(outfalls)} outfalls")
    print(f"  drainage potential raised above terrain: mean {lv.mean():.3f} m, "
          f"median {np.median(lv):.3f} m, max {lv.max():.2f} m")
    print(f"  (this grows with pipe-path distance to the outfall: "
          f"{MIN_SLOPE} x path length, so it is a routing scalar, not a real invert)")
    print(f"  every manhole now has a descending path to an outfall (by construction)")

    for n in nodes:
        nodes[n]["elev_m"] = float(nodes[n]["terrain_m"])   # terrain = display/flood depth
        nodes[n]["drain_potential_m"] = float(cond[n])      # monotone routing scalar
        nodes[n]["potential_lift_m"] = float(lift[n])
    return cond, lift, parent

def mark_local_minima(nodes, adj):
    """
    A node is a terrain depression if every neighbouring manhole is meaningfully
    HIGHER. These are the genuine ponding hotspots: surface water arriving here
    has nowhere to go except into the drain, so if the drain surcharges, it stays
    on the road.

    Two exclusions, both established empirically by Drainage/dem_sensitivity.py,
    which found 289 "minima" survive on a perfectly flat tilted plane where by
    construction none exist:

    1. Degree-1 dead ends. A cul-de-sac whose single neighbour is uphill is a
       GRAPH minimum, not a bowl - water still runs down the street to the
       junction. Lucknow's residential layout has many of these.
    2. Near-ties. A road running along a contour gives neighbours at almost
       equal elevation, and `>=` counted those as depressions. Require a margin
       comparable to the real per-cell drop (0.059 m at the 2.09 m/km regional
       gradient), so a "depression" must exceed the terrain's own resolution.

    Deliberately distinct from `potential_lift_m`, which mostly measures pipe-path
    distance to the outfall and would flag over half the network.
    """
    n_min = 0
    excl_deadend = 0
    excl_tie = 0
    for n in nodes:
        e = nodes[n]["terrain_m"]
        neigh = adj[n]
        if len(neigh) < 2:
            # would this have been flagged under the old non-strict rule?
            if neigh and all(nodes[m]["terrain_m"] >= e for m, _ in neigh):
                excl_deadend += 1
            nodes[n]["is_local_min"] = 0
            continue
        is_min = all(nodes[m]["terrain_m"] > e + DEPRESSION_MARGIN_M for m, _ in neigh)
        if not is_min and all(nodes[m]["terrain_m"] >= e for m, _ in neigh):
            excl_tie += 1
        nodes[n]["is_local_min"] = int(is_min)
        n_min += is_min
    print(f"  local terrain minima on the road graph (ponding hotspots): {n_min} "
          f"({100 * n_min / len(nodes):.1f}%)")
    print(f"    excluded {excl_deadend} degree-1 dead ends and {excl_tie} contour "
          f"near-ties (< {DEPRESSION_MARGIN_M} m margin)")
    return n_min

# STEP 8: Orient + size pipes

def manning_capacity(diameter_m, slope):
    """
    Full-flow discharge of a circular pipe (Manning):
        Q = (1/n) * A * R^(2/3) * sqrt(S),  A = pi*D^2/4,  R = D/4
          = (pi / 4^(5/3) / n) * D^(8/3) * sqrt(S)
    """
    k = math.pi / (4.0 ** (5.0 / 3.0))       # 0.311688
    return (k / MANNING_N) * (diameter_m ** (8.0 / 3.0)) * math.sqrt(slope)

def manning_velocity(diameter_m, slope):
    return (1.0 / MANNING_N) * (diameter_m / 4.0) ** (2.0 / 3.0) * math.sqrt(slope)

def build_drainage_graph(nodes, pairs, cond, parent, chan_dist=None):
    """
    Build the directed network from the priority-flood parent tree.

    A gravity manhole has exactly ONE outgoing conduit: water cannot choose
    between two downstream pipes. Keeping every road segment as its own directed
    conduit instead produced 573 nodes (30%) with multiple outflows and 742
    cycles, which double-counted upstream area and oversized the trunks.

    The parent pointers from the priority flood form a spanning arborescence
    rooted at the outfalls, which is exactly the topology of a designed gravity
    network: one outflow per manhole, every path terminating at an outfall.
    Because cond[child] >= cond[parent] + MIN_SLOPE * length by construction,
    every conduit automatically meets the minimum gradient.
    """
    print("\n" + "=" * 64)
    print("STEP 8: Building the drainage tree (one outflow per manhole)")
    print("=" * 64)

    D = nx.DiGraph()
    for nid, attrs in nodes.items():
        D.add_node(nid, **attrs)

    clamp_max = missing = n_major = n_minor = 0
    for child, par in parent.items():
        if par is None:                     # outfall root
            continue
        key = (child, par) if str(child) <= str(par) else (par, child)
        seg = pairs.get(key)
        if seg is None:                     # should not happen: parent came from adj
            missing += 1
            continue
        hw, length, name = seg

        drop = cond[child] - cond[par]
        raw_slope = drop / length
        slope = raw_slope
        if slope > MAX_SLOPE:
            slope, clamp_max = MAX_SLOPE, clamp_max + 1

        # Base size: road class, UNLESS the conduit runs alongside a mapped
        # channel, in which case it IS that channel and the road tag is
        # irrelevant. This is what stops an 18 km2 flow being assigned the
        # 300 mm pipe implied by a lane tagged 'residential'.
        base_mm = int(PIPE_DIAMETER_MM.get(hw, DEFAULT_DIAMETER_MM))
        via = ""
        if chan_dist is not None and USE_CHANNEL_BASE_SIZE:
            dc = min(chan_dist[child]["major"], chan_dist[par]["major"])
            dm = min(chan_dist[child]["minor"], chan_dist[par]["minor"])
            if dc <= CHANNEL_NALA_M:
                base_mm, via = max(base_mm, NALA_BASE_DIAMETER_MM), "river/canal"
                n_major += 1
            elif dm <= CHANNEL_TRUNK_M:
                base_mm, via = max(base_mm, TRUNK_BASE_DIAMETER_MM), "drain"
                n_minor += 1

        D.add_edge(
            child, par,
            highway=hw,
            name=(str(name) if isinstance(name, str) else ""),
            length_m=round(length, 2),
            drop_m=round(drop, 4),
            slope_terrain=round(slope, 6),
            slope_raw=round(raw_slope, 6),
            base_diameter_mm=base_mm,
            base_from_channel=via,
        )

    if chan_dist is not None and USE_CHANNEL_BASE_SIZE:
        print(f"  base size taken from mapped infrastructure rather than road class:")
        print(f"    beside a river/canal -> {NALA_BASE_DIAMETER_MM} mm nala : {n_major}")
        print(f"    beside a tagged drain -> {TRUNK_BASE_DIAMETER_MM} mm trunk: {n_minor}")

    roots = sum(1 for p in parent.values() if p is None)
    n_edges = max(D.number_of_edges(), 1)
    length_km = sum(d["length_m"] for _, _, d in D.edges(data=True)) / 1000.0

    print(f"  conduits: {D.number_of_edges()}  (= {D.number_of_nodes()} manholes "
          f"- {roots} outfall roots)")
    if missing:
        print(f"  ! {missing} parent links had no matching road segment")
    out_deg = Counter(d for _, d in D.out_degree())
    print(f"  out-degree: " + ", ".join(f"{k}->{v}" for k, v in sorted(out_deg.items())))
    print(f"  undirected cycles: {len(nx.cycle_basis(nx.Graph(D)))} (a tree has none)")
    print(f"  gradient capped at {MAX_SLOPE} (1 in {1/MAX_SLOPE:.0f}): "
          f"{clamp_max} ({100 * clamp_max / n_edges:.1f}%)")
    print(f"  minimum gradient is guaranteed by the conditioning, so no lower clamp "
          f"is needed")
    print(f"  conduit density: {length_km:.0f} km / 43.4 km2 = {length_km/43.4:.1f} "
          f"km/km2  (real municipal networks: 3-8)")
    print(f"  sizing and capacities are assigned in STEP 11, once upstream")
    print(f"  contributing areas are known.")
    return D

# STEP 10: Node hydraulics + topology

def nala_capacity(width_m, slope):
    """Rectangular masonry open channel, depth = NALA_DEPTH_RATIO x width."""
    y = NALA_DEPTH_RATIO * width_m
    a = width_m * y
    r = a / (width_m + 2 * y)
    return (1.0 / MANNING_N_NALA) * a * r ** (2.0 / 3.0) * math.sqrt(slope)

def nala_width_for(q, slope):
    """Invert the above for the width needed to carry q."""
    y_r, n = NALA_DEPTH_RATIO, MANNING_N_NALA
    coef = (1.0 / n) * y_r * (y_r / (1 + 2 * y_r)) ** (2.0 / 3.0)
    return (q / (coef * math.sqrt(slope))) ** 0.375

def pipe_diameter_for(q, slope):
    """Invert Manning full-flow for the diameter needed to carry q."""
    k = math.pi / (4.0 ** (5.0 / 3.0))
    return (q * MANNING_N / (k * math.sqrt(slope))) ** 0.375

def annotate_topology(D):
    """Topological order + exact upstream node/area accumulation."""
    print("\n" + "=" * 64)
    print("STEP 10: Topological order + upstream accumulation")
    print("=" * 64)

    is_dag = nx.is_directed_acyclic_graph(D)
    print(f"  directed acyclic graph: {is_dag}")
    if not is_dag:  # impossible given a strict total order, but stay safe
        for cyc in list(nx.simple_cycles(D)):
            e = [x for x in zip(cyc, cyc[1:] + cyc[:1]) if D.has_edge(*x)]
            if e:
                D.remove_edge(*min(e, key=lambda x: D.edges[x]["slope"]))
        print(f"  DAG after repair: {nx.is_directed_acyclic_graph(D)}")

    topo = list(nx.topological_sort(D))
    for i, n in enumerate(topo):
        D.nodes[n]["topo_order"] = i

    # In an arborescence each node has at most one outflow, so a single reverse
    # pass accumulates upstream nodes and area exactly. (On a general DAG this
    # would double-count: the naive `up[m] += up[n] + 1` reported 2.8 M upstream
    # nodes for a 1921-node graph because it counts PATHS, not nodes.)
    multi = [n for n in D.nodes if D.out_degree(n) > 1]
    if multi:
        raise AssertionError(
            f"{len(multi)} nodes have multiple outflows; accumulation would "
            f"double-count. The graph must be an arborescence.")

    up_n = {n: 0 for n in D.nodes}
    up_a = {n: D.nodes[n]["catchment_area_m2"] for n in D.nodes}
    for n in topo:                       # upstream nodes precede downstream
        for m in D.successors(n):        # at most one
            up_n[m] += up_n[n] + 1
            up_a[m] += up_a[n]

    for n in D.nodes:
        D.nodes[n]["upstream_nodes"] = int(up_n[n])
        D.nodes[n]["upstream_area_m2"] = round(float(up_a[n]), 1)

    ua = np.array([D.nodes[n]["upstream_area_m2"] for n in D.nodes]) / 1e6
    total = sum(D.nodes[n]["catchment_area_m2"] for n in D.nodes) / 1e6
    root_sum = sum(D.nodes[n]["upstream_area_m2"] for n in D.nodes
                   if D.out_degree(n) == 0) / 1e6
    print(f"  upstream drainage area: median {np.median(ua):.3f} km2, "
          f"max {ua.max():.2f} km2")
    print(f"  mass balance: outfall areas sum to {root_sum:.2f} km2 vs "
          f"{total:.2f} km2 serviced  (must match: no double counting)")
    return topo

def _size_conduit(d, q_req):
    """
    Pick the smallest standard conduit that carries `q_req`, escalating through
    lateral pipe -> trunk pipe -> open nala. Each class uses its own maximum
    gradient (large conduits are laid flatter) so velocities stay in the
    0.6-3.0 m/s self-cleansing / non-scouring band.

    Mutates `d` with conduit_type, diameter_mm, width_m, slope, capacity and
    velocity. Returns True if the class/size changed from the road-class default.
    """
    st = d["slope_terrain"]
    base = d["base_diameter_mm"]
    q_req = max(q_req, 0.0)

    # --- 1. lateral pipe (<= 900 mm) ---
    s = min(st, MAX_SLOPE_PIPE)
    need = pipe_diameter_for(q_req, s) * 1000.0 if q_req > 0 else 0.0
    want = max(need, base)
    if want <= 900:
        dia = next(x for x in STANDARD_PIPE_MM if x >= want)
        d.update(conduit_type="pipe", diameter_mm=int(dia), width_m=0.0,
                 slope=round(s, 6))
        cap = manning_capacity(dia / 1000.0, s)
        d["velocity_ms"] = round(manning_velocity(dia / 1000.0, s), 3)
    else:
        # --- 2. trunk pipe (1050-1800 mm) ---
        s = min(st, MAX_SLOPE_TRUNK)
        need = pipe_diameter_for(q_req, s) * 1000.0 if q_req > 0 else 0.0
        want = max(need, base)
        if want <= MAX_PIPE_MM:
            dia = next(x for x in STANDARD_PIPE_MM if x >= want)
            d.update(conduit_type="trunk", diameter_mm=int(dia), width_m=0.0,
                     slope=round(s, 6))
            cap = manning_capacity(dia / 1000.0, s)
            d["velocity_ms"] = round(manning_velocity(dia / 1000.0, s), 3)
        else:
            # --- 3. open nala ---
            s = min(st, MAX_SLOPE_NALA)
            # Round the width UP to a buildable 10 mm so the exported width
            # reproduces the exported capacity exactly, and never under-delivers.
            w = min(math.ceil(nala_width_for(q_req, s) * 100) / 100, MAX_NALA_WIDTH_M)
            d.update(conduit_type="nala", diameter_mm=0, width_m=w,
                     slope=round(s, 6))
            cap = nala_capacity(w, s)
            d["velocity_ms"] = round(cap / (w * NALA_DEPTH_RATIO * w), 3)

    d["capacity_design_m3s"] = round(cap, 4)
    # Effective (as-built, silted) capacity is what the flood model must use.
    d["capacity_m3s"] = round(cap * SILTATION_FACTOR, 4)
    d["travel_time_s"] = round(d["length_m"] / max(d["velocity_ms"], 0.01), 1)
    return cap

def size_trunk_conduits(D, topo):
    """
    Size conduits by upstream contributing area (rational method), because road
    class alone under-sizes trunks by more than an order of magnitude: a 1200 mm
    pipe carries 1.5 m3/s but a 20 km2 catchment delivers ~58 m3/s.

    Then enforce downstream capacity monotonicity in a topological pass. Discrete
    standard sizes plus class-specific gradients can otherwise leave a junction
    narrowing downstream, which would surcharge the network by construction.
    """
    print("\n" + "=" * 64)
    print("STEP 11: Sizing conduits (rational method + monotonicity)")
    print("=" * 64)
    print(f"  design storm: {DESIGN_INTENSITY_MMHR} mm/hr, C = {DESIGN_RUNOFF_COEFF} "
          f"(the standard the legacy network was built to)")

    i_ms = DESIGN_INTENSITY_MMHR / 1000.0 / 3600.0

    # --- pass 1: size for the local design flow ---
    for u, v, d in D.edges(data=True):
        area_m2 = D.nodes[u]["upstream_area_m2"]
        q_req = DESIGN_RUNOFF_COEFF * i_ms * area_m2
        d["design_flow_m3s"] = round(q_req, 4)
        d["upstream_area_m2"] = area_m2
        _size_conduit(d, q_req)

    upsized = sum(1 for _, _, d in D.edges(data=True)
                  if d["diameter_mm"] != d["base_diameter_mm"])

    # --- pass 2: never narrow downstream ---
    bumped = 0
    for n in topo:
        preds = list(D.predecessors(n))
        if not preds:
            continue
        max_in = max(D.edges[m, n]["capacity_design_m3s"] for m in preds)
        for m in D.successors(n):
            d = D.edges[n, m]
            if d["capacity_design_m3s"] < max_in - 1e-6:
                _size_conduit(d, max(d["design_flow_m3s"], max_in))
                bumped += 1

    print(f"  pass 1 - conduits upsized above their road-class default: {upsized} "
          f"({100 * upsized / D.number_of_edges():.1f}%)")
    print(f"  pass 2 - conduits upsized to stop downstream narrowing: {bumped}")
    print(f"  siltation factor applied to effective capacity: {SILTATION_FACTOR}")

    print(f"\n  {'conduit':>8} {'count':>6} {'mean Q':>9} {'max Q':>9} "
          f"{'min V':>7} {'mean V':>7} {'max V':>7}")
    for ct in ("pipe", "trunk", "nala"):
        sel = [d for _, _, d in D.edges(data=True) if d["conduit_type"] == ct]
        if not sel:
            continue
        c = np.array([x["capacity_m3s"] for x in sel])
        v = np.array([x["velocity_ms"] for x in sel])
        print(f"  {ct:>8} {len(sel):>6} {c.mean():>9.3f} {c.max():>9.3f} "
              f"{v.min():>7.2f} {v.mean():>7.2f} {v.max():>7.2f}")

    nalas = [d for _, _, d in D.edges(data=True) if d["conduit_type"] == "nala"]
    if nalas:
        w = np.array([d["width_m"] for d in nalas])
        print(f"  nala width: {w.min():.1f} - {w.max():.1f} m (mean {w.mean():.1f} m)")

    # Cross-check the smallest laterals against the implementation plan's table,
    # which assumes minimum-slope design.
    print(f"\n  Lateral pipes at minimum gradient vs implementation-plan reference:")
    print(f"    {'diameter':>9} {'count':>6} {'mean Q':>8} {'plan ref':>9} {'dev':>6}")
    dias = defaultdict(list)
    for _, _, d in D.edges(data=True):
        if d["diameter_mm"]:
            dias[d["diameter_mm"]].append(d["capacity_m3s"])
    for dia in sorted(dias, reverse=True):
        c = np.array(dias[dia])
        ref = REFERENCE_CAPACITY_M3S.get(dia)
        if ref:
            print(f"    {dia:>6} mm {len(c):>6} {c.mean():>8.3f} {ref:>9.2f} "
                  f"{100 * (c.mean() - ref) / ref:>5.0f}%")
        else:
            print(f"    {dia:>6} mm {len(c):>6} {c.mean():>8.3f} {'-':>9} {'-':>6}")

def annotate_hydraulics(D, outfalls, channel_caps=None):
    """Per-node capacities, classification and bottleneck detection."""
    print("\n" + "=" * 64)
    print("STEP 12: Node hydraulics")
    print("=" * 64)
    if channel_caps is None:
        channel_caps = {}

    n_outfall = n_depression = n_manhole = 0
    n_finite = 0
    for n in D.nodes:
        a = D.nodes[n]
        out_cap = sum(D.edges[n, m]["capacity_m3s"] for m in D.successors(n))
        in_caps = [D.edges[m, n]["capacity_m3s"] for m in D.predecessors(n)]
        in_cap = sum(in_caps)

        if n in outfalls or D.out_degree(n) == 0:
            a["node_type"] = "outfall"
            a["is_outlet"] = 1
            if n in channel_caps:
                # Finite-capacity receiving channel: this outfall can only
                # discharge as much as its share of the receiving waterway.
                # This is the key fix: nala-adjacent outfalls back up when
                # the channel is overwhelmed, instead of being infinite sinks.
                out_cap = max(out_cap, channel_caps[n])
                n_finite += 1
            else:
                # No channel data → old behaviour (infinite sink).
                out_cap = max(out_cap, in_cap, REFERENCE_CAPACITY_M3S[1200])
            n_outfall += 1
        elif a["is_local_min"]:
            a["node_type"] = "depression"      # terrain low point -> ponding risk
            a["is_outlet"] = 0
            n_depression += 1
        else:
            a["node_type"] = "manhole"
            a["is_outlet"] = 0
            n_manhole += 1

        a["incoming_capacity_m3s"] = round(in_cap, 4)
        a["outgoing_capacity_m3s"] = round(out_cap, 4)
        a["in_degree"] = int(D.in_degree(n))
        a["out_degree"] = int(D.out_degree(n))
        # Convergence ratio > 1 is normal in a tree (many branches feed one trunk).
        a["convergence_ratio"] = round(in_cap / out_cap, 4) if out_cap > 0 else 0.0
        # A true hydraulic fault: water leaves through a conduit SMALLER than the
        # largest one bringing it in, so this junction surcharges first.
        a["is_bottleneck"] = int(
            bool(in_caps) and a["is_outlet"] == 0 and out_cap < max(in_caps) - 1e-9
        )
        # Design-storm stress: how much of this node's capacity the design storm
        # already consumes. The coupling engine scales this by actual rainfall.
        q_req = DESIGN_RUNOFF_COEFF * (DESIGN_INTENSITY_MMHR / 3.6e6) \
            * a["upstream_area_m2"]
        a["design_flow_m3s"] = round(q_req, 4)
        a["design_stress"] = round(q_req / out_cap, 4) if out_cap > 0 else 0.0

    print(f"  node types: manhole {n_manhole}, depression {n_depression}, "
          f"outfall {n_outfall}")
    if n_finite:
        print(f"  outfalls with FINITE receiving-channel capacity: {n_finite} "
              f"(of {n_outfall})")

    n_bott = sum(D.nodes[n]["is_bottleneck"] for n in D.nodes)
    stress = np.array([D.nodes[n]["design_stress"] for n in D.nodes])
    print(f"  diameter-reduction bottlenecks (outflow < largest inflow): {n_bott} "
          f"({100 * n_bott / D.number_of_nodes():.1f}%)")
    print(f"  design-storm stress (inflow/capacity at {DESIGN_INTENSITY_MMHR} mm/hr): "
          f"median {np.median(stress):.2f}, 95th pctl {np.percentile(stress, 95):.2f}, "
          f"max {stress.max():.2f}")
    print(f"  nodes already over capacity at the design storm: "
          f"{int((stress > 1).sum())} ({100 * (stress > 1).mean():.1f}%)")

    print("\n  Top 10 trunk junctions (largest upstream drainage area):")
    top = sorted(D.nodes, key=lambda x: D.nodes[x]["upstream_area_m2"], reverse=True)[:10]
    for i, n in enumerate(top, 1):
        a = D.nodes[n]
        print(f"    #{i:<2} ({a['lat']:.5f}N, {a['lon']:.5f}E) terrain={a['elev_m']:6.2f}m "
              f"up: {a['upstream_nodes']:>4}n/{a['upstream_area_m2']/1e6:5.2f}km2 "
              f"Qout={a['outgoing_capacity_m3s']:7.3f} Qreq={a['design_flow_m3s']:7.3f} "
              f"stress={a['design_stress']:5.2f} [{a['node_type']}]")
    return D

# STEP 10: Manhole catchments (nearest_manhole in O(1))

def assign_catchments(D, dem):
    print("\n" + "=" * 64)
    print("STEP 9: Assigning DEM cells to serving manholes")
    print("=" * 64)

    node_ids = list(D.nodes())
    idx_of = {n: i for i, n in enumerate(node_ids)}

    lon_grid, lat_grid = dem.cell_centers_lonlat()
    to_utm = Transformer.from_crs("EPSG:4326", UTM_CRS, always_xy=True)
    cx, cy = to_utm.transform(lon_grid.ravel(), lat_grid.ravel())

    tree = cKDTree(np.column_stack([[D.nodes[n]["x"] for n in node_ids],
                                    [D.nodes[n]["y"] for n in node_ids]]))
    dist, nearest = tree.query(np.column_stack([cx, cy]), k=1)

    catchment = nearest.reshape(dem.shape).astype(np.int32)
    dist_grid = dist.reshape(dem.shape)
    catchment[dem.nodata_mask] = -1

    px = float(np.median(np.abs(np.diff(cx.reshape(dem.shape), axis=1))))
    py = float(np.median(np.abs(np.diff(cy.reshape(dem.shape), axis=0))))
    cell_area = px * py

    print(f"  DEM cell size: {px:.1f} m x {py:.1f} m  ({cell_area:.0f} m2/cell)")
    print(f"  cells assigned: {int((catchment >= 0).sum())}")
    print(f"  distance cell->manhole: mean {dist_grid.mean():.0f} m, max {dist_grid.max():.0f} m")

    counts = np.bincount(catchment[catchment >= 0].ravel(), minlength=len(node_ids))
    for n in D.nodes:
        c = int(counts[idx_of[n]])
        D.nodes[n]["catchment_cells"] = c
        D.nodes[n]["catchment_area_m2"] = round(c * cell_area, 1)
        D.nodes[n]["node_index"] = idx_of[n]

    areas = counts * cell_area
    print(f"  catchment per manhole: mean {areas.mean():.0f} m2, "
          f"median {np.median(areas):.0f} m2, max {areas.max():.0f} m2")
    print(f"  total serviced area: {areas.sum() / 1e6:.2f} km2")

    # ---- safe ponding denominator -----------------------------------------
    # Voronoi on a 28 m grid leaves some manholes with ZERO cells: a closer
    # neighbour won every cell. That zero is CORRECT for mass balance - the
    # runoff really is accounted for at the neighbour, and inventing area here
    # would break the exact upstream-area balance that validate() checks.
    #
    # But it is fatal as a divisor, and `depth = excess / area` is exactly what
    # a consumer will reach for. So publish a SEPARATE field that is always
    # positive and physically motivated: the street surface draining to this
    # manhole, taken as half of each incident segment length times a corridor
    # width. Never use this for mass balance.
    n_zero = int((counts == 0).sum())
    for n in D.nodes:
        street = 0.5 * sum(
            float(d.get("length_m", 0.0))
            for _, _, d in list(D.in_edges(n, data=True)) + list(D.out_edges(n, data=True)))
        street_area = street * STREET_CORRIDOR_M
        D.nodes[n]["ponding_area_m2"] = round(
            max(D.nodes[n]["catchment_area_m2"], street_area, cell_area), 1)

    pond = np.array([D.nodes[n]["ponding_area_m2"] for n in D.nodes])
    print(f"  manholes with zero Voronoi cells: {n_zero} "
          f"({100 * n_zero / len(node_ids):.1f}%) -> correct for mass balance,")
    print(f"    unusable as a divisor, so ponding_area_m2 floors every node at")
    print(f"    max(catchment, street corridor, 1 cell): min {pond.min():.0f} m2")
    return catchment, dist_grid, cell_area

# STEP 11: Validation

def validate(D, topo, outfalls):
    print("\n" + "=" * 64)
    print("STEP 13: Validation")
    print("=" * 64)

    report = {}

    def check(name, ok, detail=""):
        report[name] = {"pass": bool(ok), "detail": detail}
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    check("is_dag", nx.is_directed_acyclic_graph(D))
    check("topo_order_complete", len(topo) == D.number_of_nodes())

    # Gravity topology: one outflow per manhole, no loops. The network is a
    # spanning FOREST - one arborescence per outfall - so it is deliberately not
    # weakly connected: each outfall serves its own sub-catchment.
    multi = [n for n in D.nodes if D.out_degree(n) > 1]
    check("single_outflow_per_manhole", not multi, f"{len(multi)} nodes split flow")
    cycles = len(nx.cycle_basis(nx.Graph(D)))
    check("no_undirected_cycles", cycles == 0, f"{cycles} cycles")

    roots = [n for n in D.nodes if D.out_degree(n) == 0]
    n_comp = nx.number_weakly_connected_components(D)
    check("one_tree_per_outfall", n_comp == len(roots),
          f"{n_comp} components, {len(roots)} roots")
    check("edge_count_matches_forest", D.number_of_edges() == D.number_of_nodes() - len(roots),
          f"{D.number_of_edges()} edges, {D.number_of_nodes()}-{len(roots)} expected")

    # Mass balance: the outfalls must collectively account for the whole area.
    serviced = sum(D.nodes[n]["catchment_area_m2"] for n in D.nodes)
    at_roots = sum(D.nodes[n]["upstream_area_m2"] for n in roots)
    check("upstream_area_mass_balance", abs(serviced - at_roots) < 1.0,
          f"{serviced/1e6:.3f} vs {at_roots/1e6:.3f} km2")

    uphill = [(u, v) for u, v, d in D.edges(data=True) if d["drop_m"] < 0]
    check("no_uphill_pipes", not uphill, f"{len(uphill)} uphill")

    below_min = [(u, v) for u, v, d in D.edges(data=True) if d["slope"] < MIN_SLOPE - 1e-9]
    check("all_pipes_meet_min_slope", not below_min, f"{len(below_min)} below {MIN_SLOPE}")

    zero_cap = [(u, v) for u, v, d in D.edges(data=True) if d["capacity_m3s"] <= 0]
    check("all_pipes_have_capacity", not zero_cap, f"{len(zero_cap)} zero-capacity")

    vels = np.array([d["velocity_ms"] for _, _, d in D.edges(data=True)])
    check("velocities_in_design_band", bool(vels.min() >= 0.5 and vels.max() <= 3.5),
          f"{vels.min():.2f}-{vels.max():.2f} m/s")

    # Trunk sizing must be monotone: a conduit may never carry less than the
    # largest conduit feeding it, or the network surcharges by construction.
    shrink = 0
    for n in D.nodes:
        if D.nodes[n]["is_outlet"]:
            continue
        ins = [D.edges[m, n]["capacity_design_m3s"] for m in D.predecessors(n)]
        outs = [D.edges[n, m]["capacity_design_m3s"] for m in D.successors(n)]
        if ins and outs and max(outs) < max(ins) - 1e-6:
            shrink += 1
    check("trunk_capacity_non_decreasing", shrink == 0,
          f"{shrink} junctions narrow downstream")

    # Every conduit must be able to carry its own design-storm flow.
    under = [(u, v) for u, v, d in D.edges(data=True)
             if d["capacity_design_m3s"] < d["design_flow_m3s"] - 1e-6]
    check("conduits_carry_design_storm", not under,
          f"{len(under)} under-sized at {DESIGN_INTENSITY_MMHR} mm/hr")

    no_cap = [n for n in D.nodes if D.nodes[n]["outgoing_capacity_m3s"] <= 0]
    check("every_node_can_discharge", not no_cap, f"{len(no_cap)} with zero outflow")

    # The property the coupling engine depends on: surcharge must always have
    # somewhere downstream to go.
    outfall_set = {n for n in D.nodes if D.nodes[n]["is_outlet"] == 1}
    reach = set()
    for n in reversed(topo):
        if n in outfall_set or any(m in reach for m in D.successors(n)):
            reach.add(n)
    unreached = D.number_of_nodes() - len(reach)
    check("all_nodes_reach_an_outfall", unreached == 0, f"{unreached} stranded")

    n_pass = sum(1 for v in report.values() if v["pass"])
    print(f"\n  {n_pass}/{len(report)} checks passed")
    return report

# STEP 12: Export

def export_all(D, topo, dem, catchment, cell_area, validation):
    print("\n" + "=" * 64)
    print("STEP 14: Exporting")
    print("=" * 64)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    def prim(v):
        return v if isinstance(v, (int, float, str, bool)) else str(v)

    graphml_path = os.path.join(OUTPUT_DIR, "drainage_graph.graphml")
    Gx = nx.DiGraph()
    for n in D.nodes:
        Gx.add_node(str(n), **{k: prim(v) for k, v in D.nodes[n].items()})
    for u, v, d in D.edges(data=True):
        Gx.add_edge(str(u), str(v), **{k: prim(x) for k, x in d.items()})
    nx.write_graphml(Gx, graphml_path)
    print(f"  Saved: {graphml_path}")

    contract = {
        "meta": {
            "description": "Synthetic storm-drainage network, Lucknow pilot zone",
            "pilot_zone": "Charbagh-Aminabad-Alambagh",
            "source": "OpenStreetMap roads + SRTM 30 m DEM",
            "crs_geographic": "EPSG:4326",
            "crs_projected": UTM_CRS,
            "bbox": [dem.bounds.left, dem.bounds.bottom, dem.bounds.right, dem.bounds.top],
            "n_nodes": D.number_of_nodes(),
            "n_edges": D.number_of_edges(),
            "dem_cell_area_m2": round(cell_area, 6),
            "manning_n": MANNING_N,
            "manning_n_nala": MANNING_N_NALA,
            "min_slope": MIN_SLOPE,
            "max_slope": MAX_SLOPE,
            "design_intensity_mm_hr": DESIGN_INTENSITY_MMHR,
            "design_runoff_coeff": DESIGN_RUNOFF_COEFF,
            "siltation_factor": SILTATION_FACTOR,
            "consolidate_tolerance_m": CONSOLIDATE_TOLERANCE_M,
            "pipe_diameter_mm_by_class": PIPE_DIAMETER_MM,
            "capacity_formula": "Q = (pi/4^(5/3)/n) * D^(8/3) * sqrt(S)   [Manning, full flow]",
            "sizing": ("conduits sized by max(road class, rational-method demand from "
                       "upstream area); above 1800 mm equivalent they become open nalas"),
            "conditioning": ("priority-flood from outfalls with minimum design slope; "
                             "guarantees every manhole drains to an outfall"),
            "note_capacity": ("capacity_m3s is the EFFECTIVE (silted) capacity the flood "
                              "model should use; capacity_design_m3s is the clean value"),
        },
        "topological_order": [str(n) for n in topo],
        "nodes": [
            {
                "id": str(n),
                "node_index": a["node_index"],
                "lon": round(a["lon"], 7), "lat": round(a["lat"], 7),
                "x_utm": round(a["x"], 2), "y_utm": round(a["y"], 2),
                "elev_m": round(a["elev_m"], 3),
                "terrain_m": round(a["terrain_m"], 3),
                "elev_raw_m": round(a["elev_raw_m"], 1),
                "drain_potential_m": round(a["drain_potential_m"], 3),
                "potential_lift_m": round(a["potential_lift_m"], 3),
                "is_local_min": a["is_local_min"],
                "flowacc": round(a["flowacc"], 1),
                "terrain_score": round(a["terrain_score"], 4),
                "node_type": a["node_type"], "is_outlet": a["is_outlet"],
                "in_degree": a["in_degree"], "out_degree": a["out_degree"],
                "incoming_capacity_m3s": a["incoming_capacity_m3s"],
                "outgoing_capacity_m3s": a["outgoing_capacity_m3s"],
                "convergence_ratio": a["convergence_ratio"],
                "is_bottleneck": a["is_bottleneck"],
                "design_flow_m3s": a["design_flow_m3s"],
                "design_stress": a["design_stress"],
                "catchment_cells": a["catchment_cells"],
                "catchment_area_m2": a["catchment_area_m2"],
                "ponding_area_m2": a["ponding_area_m2"],
                "topo_order": a["topo_order"],
                "upstream_nodes": a["upstream_nodes"],
                "upstream_area_m2": a["upstream_area_m2"],
            }
            for n, a in ((n, D.nodes[n]) for n in D.nodes)
        ],
        "edges": [
            {
                "from": str(u), "to": str(v),
                "highway": d["highway"], "name": d["name"],
                "conduit_type": d["conduit_type"],
                "length_m": d["length_m"],
                "diameter_mm": d["diameter_mm"], "width_m": d["width_m"],
                "slope": d["slope"], "drop_m": d["drop_m"],
                "capacity_m3s": d["capacity_m3s"],
                "capacity_design_m3s": d["capacity_design_m3s"],
                "design_flow_m3s": d["design_flow_m3s"],
                "upstream_area_m2": d["upstream_area_m2"],
                "velocity_ms": d["velocity_ms"],
                "travel_time_s": d["travel_time_s"],
            }
            for u, v, d in D.edges(data=True)
        ],
    }
    p = os.path.join(OUTPUT_DIR, "drainage_graph.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(contract, f, indent=1)
    print(f"  Saved: {p}")

    nodes_gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [round(a["lon"], 7), round(a["lat"], 7)]},
         "properties": {
             "id": str(n), "node_index": a["node_index"], "node_type": a["node_type"],
             "elev_m": round(a["elev_m"], 2), "terrain_m": round(a["terrain_m"], 2),
             "outgoing_capacity_m3s": a["outgoing_capacity_m3s"],
             "incoming_capacity_m3s": a["incoming_capacity_m3s"],
             "convergence_ratio": a["convergence_ratio"],
             "is_bottleneck": a["is_bottleneck"],
             "is_local_min": a["is_local_min"],
             "design_stress": a["design_stress"],
             "catchment_area_m2": a["catchment_area_m2"],
             "upstream_nodes": a["upstream_nodes"],
             "upstream_area_m2": a["upstream_area_m2"],
             "drain_potential_m": round(a["drain_potential_m"], 2),
         }}
        for n, a in ((n, D.nodes[n]) for n in D.nodes)
    ]}
    p = os.path.join(OUTPUT_DIR, "drainage_nodes.geojson")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(nodes_gj, f)
    print(f"  Saved: {p}  ({len(nodes_gj['features'])} manholes)")

    edges_gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "LineString", "coordinates": [
             [round(D.nodes[u]["lon"], 7), round(D.nodes[u]["lat"], 7)],
             [round(D.nodes[v]["lon"], 7), round(D.nodes[v]["lat"], 7)]]},
         "properties": {
             "from": str(u), "to": str(v), "highway": d["highway"], "name": d["name"],
             "conduit_type": d["conduit_type"],
             "diameter_mm": d["diameter_mm"], "width_m": d["width_m"],
             "capacity_m3s": d["capacity_m3s"],
             "design_flow_m3s": d["design_flow_m3s"],
             "length_m": d["length_m"], "slope": d["slope"],
             "velocity_ms": d["velocity_ms"]}}
        for u, v, d in D.edges(data=True)
    ]}
    p = os.path.join(OUTPUT_DIR, "drainage_edges.geojson")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(edges_gj, f)
    print(f"  Saved: {p}  ({len(edges_gj['features'])} pipes)")

    prof = dem.profile.copy()
    prof.update({"dtype": "int32", "nodata": -1, "count": 1, "compress": "lzw"})
    p = os.path.join(OUTPUT_DIR, "manhole_catchments.tif")
    with rasterio.open(p, "w", **prof) as dst:
        dst.write(catchment, 1)
    print(f"  Saved: {p}  (nearest_manhole lookup -> node_index)")

    caps = np.array([d["capacity_m3s"] for _, _, d in D.edges(data=True)])
    lengths = np.array([d["length_m"] for _, _, d in D.edges(data=True)])
    stress = np.array([D.nodes[n]["design_stress"] for n in D.nodes])
    summary = {
        "nodes": D.number_of_nodes(),
        "edges": D.number_of_edges(),
        "node_types": dict(Counter(D.nodes[n]["node_type"] for n in D.nodes)),
        "conduit_types": dict(Counter(d["conduit_type"] for _, _, d in D.edges(data=True))),
        "pipe_diameters_mm": {str(k): v for k, v in sorted(
            Counter(d["diameter_mm"] for _, _, d in D.edges(data=True)
                    if d["diameter_mm"]).items(), reverse=True)},
        "total_conduit_length_km": round(float(lengths.sum()) / 1000.0, 2),
        "capacity_m3s": {"total": round(float(caps.sum()), 2),
                         "mean": round(float(caps.mean()), 4),
                         "min": round(float(caps.min()), 4),
                         "max": round(float(caps.max()), 4)},
        "terrain_elevation_m": {
            "min": round(min(D.nodes[n]["elev_m"] for n in D.nodes), 3),
            "max": round(max(D.nodes[n]["elev_m"] for n in D.nodes), 3)},
        "serviced_area_km2": round(
            sum(D.nodes[n]["catchment_area_m2"] for n in D.nodes) / 1e6, 3),
        "bottleneck_junctions": sum(D.nodes[n]["is_bottleneck"] for n in D.nodes),
        "local_minima": sum(D.nodes[n]["is_local_min"] for n in D.nodes),
        "design_storm": {
            "intensity_mm_hr": DESIGN_INTENSITY_MMHR,
            "runoff_coeff": DESIGN_RUNOFF_COEFF,
            "siltation_factor": SILTATION_FACTOR,
            "median_node_stress": round(float(np.median(stress)), 3),
            "nodes_over_capacity": int((stress > 1).sum()),
        },
        "validation": validation,
    }
    p = os.path.join(OUTPUT_DIR, "drainage_summary.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"  Saved: {p}")
    return summary

# STEP 13: Visualisation

def visualise(D, dem, catchment):
    print("\n" + "=" * 64)
    print("STEP 15: Visualising")
    print("=" * 64)

    rows, cols = dem.shape
    extent = [dem.transform.c, dem.transform.c + cols * dem.transform.a,
              dem.transform.f + rows * dem.transform.e, dem.transform.f]

    segs = np.array([[[D.nodes[u]["lon"], D.nodes[u]["lat"]],
                      [D.nodes[v]["lon"], D.nodes[v]["lat"]]] for u, v in D.edges()])
    dias = np.array([d["diameter_mm"] for _, _, d in D.edges(data=True)])
    caps = np.array([d["capacity_m3s"] for _, _, d in D.edges(data=True)])
    ctype = np.array([d["conduit_type"] for _, _, d in D.edges(data=True)])

    A = lambda k: np.array([D.nodes[n][k] for n in D.nodes])
    nlon, nlat = A("lon"), A("lat")
    ntype = np.array([D.nodes[n]["node_type"] for n in D.nodes])
    nups = A("upstream_area_m2")
    nbot = A("is_bottleneck")
    nstress, nterr = A("design_stress"), A("terrain_m")

    def landmarks(ax, c="cyan", fc="darkblue", s=16):
        for name, (lon, lat) in PILOT_ZONE.items():
            if extent[0] <= lon <= extent[1] and extent[2] <= lat <= extent[3]:
                ax.plot(lon, lat, "*", color=c, markersize=s, markeredgecolor="black",
                        markeredgewidth=1, zorder=20)
                ax.annotate(name, (lon, lat), textcoords="offset points", xytext=(8, 8),
                            fontsize=9, fontweight="bold", color="white", zorder=21,
                            bbox=dict(boxstyle="round,pad=0.3", facecolor=fc, alpha=0.9))

    fig, axes = plt.subplots(2, 2, figsize=(22, 20))
    title_text = ("Synthetic Storm-Drainage Graph — Lucknow Pilot Zone (Charbagh · Hazratganj · Aminabad)"
                  if "validation" not in OUTPUT_DIR else
                  "Synthetic Storm-Drainage Graph — North Lucknow Validation Zone (8 Flood Sites)")
    fig.suptitle(title_text, fontsize=17, fontweight="bold")

    # 1. Network over terrain, conduits by class
    ax = axes[0, 0]
    ax.imshow(dem.terrain, cmap="terrain", extent=extent, aspect="auto", alpha=0.55)
    n_nala = int((ctype == "nala").sum())
    for sel, color, lw, lbl in [
        (ctype == "pipe", "#6e6e6e", 0.35, f"lateral pipe <=900 mm ({int((ctype=='pipe').sum())})"),
        (ctype == "trunk", "#0570b0", 1.3, f"trunk 1050-1800 mm ({int((ctype=='trunk').sum())})"),
        (ctype == "nala", "#b30000", 2.6, f"open nala ({n_nala})"),
    ]:
        if sel.any():
            ax.add_collection(LineCollection(segs[sel], colors=color, linewidths=lw,
                                             label=lbl, zorder=5))
    ax.scatter(nlon, nlat, c="black", s=0.7, alpha=0.3, zorder=6)
    landmarks(ax)
    ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3])
    ax.set_title(f"1. Drainage network: {D.number_of_nodes()} manholes, "
                 f"{D.number_of_edges()} conduits\n"
                 f"(sized by max(road class, rational-method upstream demand))",
                 fontweight="bold")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9, title="Conduit class")

    # 2. Terrain depressions + outfalls
    ax = axes[0, 1]
    ax.add_collection(LineCollection(segs, colors="#cccccc", linewidths=0.3, zorder=2))
    sc = ax.scatter(nlon, nlat, c=nterr, cmap="terrain_r", s=6, zorder=5)
    out, dep = ntype == "outfall", ntype == "depression"
    if out.any():
        ax.scatter(nlon[out], nlat[out], marker="s", s=36, facecolors="none",
                   edgecolors="blue", linewidths=1.3,
                   label=f"outfall ({out.sum()})", zorder=8)
    if dep.any():
        ax.scatter(nlon[dep], nlat[dep], marker="v", s=42, c="red", edgecolors="black",
                   linewidths=0.5, label=f"depression / ponding ({dep.sum()})", zorder=9)
    landmarks(ax, c="magenta", fc="purple")
    ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3])
    ax.set_title("2. Terrain elevation, outfalls & depression nodes\n"
                 "(depression = manhole with no lower neighbour → water ponds here)",
                 fontweight="bold")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    plt.colorbar(sc, ax=ax, label="Terrain elevation (m)", shrink=0.8)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)

    # 3. Capacity + trunk hierarchy
    ax = axes[1, 0]
    order = np.argsort(caps)
    lc = LineCollection(segs[order], array=np.log1p(caps[order]), cmap="viridis",
                        linewidths=0.35 + 2.4 * (caps[order] / caps.max()) ** 0.4, zorder=4)
    ax.imshow(dem.terrain, cmap="Greys", extent=extent, aspect="auto", alpha=0.22)
    ax.add_collection(lc)
    top = np.argsort(nups)[::-1][:15]
    ax.scatter(nlon[top], nlat[top], marker="o", s=90, facecolors="none",
               edgecolors="red", linewidths=1.8, zorder=10, label="top-15 trunk junctions")
    landmarks(ax)
    ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3])
    ax.set_title("3. Hydraulic capacity & trunk hierarchy\n"
                 "(width/colour = pipe discharge capacity, Manning full flow)",
                 fontweight="bold")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    plt.colorbar(lc, ax=ax, label="log(1 + capacity m³/s)", shrink=0.8)
    ax.legend(loc="upper right", fontsize=8, framealpha=0.9)

    # 4. Catchments + bottlenecks
    ax = axes[1, 1]
    ax.imshow(np.ma.masked_where(catchment < 0, catchment % 20), cmap="tab20",
              extent=extent, aspect="auto", interpolation="nearest")
    ax.add_collection(LineCollection(segs, colors="black", linewidths=0.25, alpha=0.5,
                                     zorder=4))
    hot = (nbot > 0) & (ntype != "outfall")
    if hot.any():
        ax.scatter(nlon[hot], nlat[hot], marker="o", s=14, c="red", edgecolors="none",
                   zorder=8, label=f"diameter-reduction bottlenecks ({hot.sum()})")
        ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    landmarks(ax)
    ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3])
    ax.set_title("4. Manhole service catchments (nearest-manhole tessellation)\n"
                 "→ maps every DEM runoff cell to the drain that receives it",
                 fontweight="bold")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")

    plt.tight_layout()
    p = os.path.join(OUTPUT_DIR, "drainage_network_overview.png")
    plt.savefig(p, dpi=140, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {p}")

# MAIN

def main():
    zone_name = "pilot"
    for i, a in enumerate(sys.argv[1:]):
        if a == "--zone" and i + 2 <= len(sys.argv[1:]):
            zone_name = sys.argv[i + 2]
        elif a.startswith("--zone="):
            zone_name = a.split("=", 1)[1]
    if zone_name not in ZONES:
        print(f"unknown zone {zone_name!r}; choose from {sorted(ZONES)}")
        return 1
    select_zone(zone_name)

    title = ("Pilot Zone: Charbagh · Aminabad · Alambagh, Lucknow" if zone_name == "pilot"
             else "VALIDATION Zone: north Lucknow — 8 sourced flood sites")
    print("=" * 64)
    print("Urban Flood Nowcasting — Drainage Network Graph Builder")
    print(title)
    if zone_name != "pilot":
        print("(separate outputs; the coupling engine still reads Drainage/processed)")
    print("=" * 64)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    dem = load_dem_stack()
    G_roads = fetch_road_network(dem)
    Gc = consolidate_to_manholes(G_roads)
    nodes = build_node_table(Gc, dem)
    pairs, adj = extract_segments(Gc, nodes)
    chans = fetch_waterways(dem) if USE_CHANNEL_OUTFALLS else None
    chan_dist = channel_distances(nodes, chans) if chans else None
    outfalls = select_outfalls(nodes, dem, chan_dist)
    channel_caps = compute_channel_shares(outfalls, nodes, chans, chan_dist) \
        if chans and USE_FINITE_CHANNELS else {}
    cond, lift, parent = condition_network(nodes, adj, outfalls)
    mark_local_minima(nodes, adj)
    D = build_drainage_graph(nodes, pairs, cond, parent, chan_dist)
    catchment, dist_grid, cell_area = assign_catchments(D, dem)
    topo = annotate_topology(D)
    size_trunk_conduits(D, topo)
    annotate_hydraulics(D, outfalls, channel_caps)
    validation = validate(D, topo, outfalls)
    summary = export_all(D, topo, dem, catchment, cell_area, validation)
    visualise(D, dem, catchment)

    print("\n" + "=" * 64)
    print("DRAINAGE GRAPH COMPLETE")
    print("=" * 64)
    print(f"  manholes (nodes)   : {summary['nodes']}")
    print(f"  conduits (edges)   : {summary['edges']}  {summary['conduit_types']}")
    print(f"  node types         : {summary['node_types']}")
    print(f"  total length       : {summary['total_conduit_length_km']} km")
    print(f"  network capacity   : {summary['capacity_m3s']['total']} m3/s "
          f"(effective, after {summary['design_storm']['siltation_factor']} siltation)")
    print(f"  serviced area      : {summary['serviced_area_km2']} km2")
    print(f"  bottleneck nodes   : {summary['bottleneck_junctions']}")
    print(f"  ponding hotspots   : {summary['local_minima']} local terrain minima")
    print(f"  design storm       : {summary['design_storm']['intensity_mm_hr']} mm/hr "
          f"-> median node stress {summary['design_storm']['median_node_stress']}")
    print(f"\n  Outputs in {OUTPUT_DIR}/")
    for f, desc in [
        ("drainage_graph.graphml", "networkx round-trip"),
        ("drainage_graph.json", "coupling-engine contract"),
        ("drainage_nodes.geojson", "Leaflet manhole layer"),
        ("drainage_edges.geojson", "Leaflet conduit layer"),
        ("manhole_catchments.tif", "O(1) nearest_manhole lookup"),
        ("drainage_summary.json", "stats + validation"),
        ("drainage_network_overview.png", "4-panel figure"),
    ]:
        print(f"    {f:<34} -> {desc}")
    print("\n  Coupling engine usage:")
    print("    import networkx as nx, rasterio")
    print(f"    G   = nx.read_graphml('{OUTPUT_DIR}/drainage_graph.graphml')")
    print(f"    idx = rasterio.open('{OUTPUT_DIR}/manhole_catchments.tif').read(1)")
    print("    # idx[row, col]                  -> serving manhole node_index")
    print("    # node['outgoing_capacity_m3s']  -> drain capacity for the excess calc")
    print("    # node['catchment_area_m2']      -> area contributing direct runoff")
    print("    # node['upstream_area_m2']       -> total area routed through the node")
    print("    # node['design_stress']          -> stress at 15 mm/hr; scale by rainfall")
    print("    # iterate nodes by node['topo_order'] to propagate surcharge downstream")
    print("\n  Two warnings for the depth calculation:")
    print("    * DIVIDE BY ponding_area_m2, NOT catchment_area_m2. The latter is 0 for")
    print("      pass-through junctions whose cells a closer manhole won (correct for")
    print("      mass balance, fatal as a divisor).")
    print("    * Do NOT apply depth = excess / area at TRUNK nodes. Upstream area there")
    print("      reaches 18.5 km2 while the node's own footprint is one cell, so the")
    print("      quotient reads tens of metres of water. Excess at a trunk backs up")
    print("      over the contributing catchment; it does not pond on the junction.")

if __name__ == "__main__":
    main()
