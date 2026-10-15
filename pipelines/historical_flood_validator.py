"""Statistical validation of drainage network against news-verified historical flood disaster sites."""

import io
import json
import math
import os
import sys

import networkx as nx
import numpy as np
from pyproj import Transformer
from scipy import stats

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ZONE = "pilot"
for _i, _a in enumerate(sys.argv[1:]):
    if _a == "--zone" and _i + 2 <= len(sys.argv[1:]):
        ZONE = sys.argv[_i + 2]
    elif _a.startswith("--zone="):
        ZONE = _a.split("=", 1)[1]

SPATIAL_DIR = os.path.join("backend", "data", "spatial", ZONE)
SITES_JSON = os.path.join("backend", "data", "spatial", "pilot", "flood_sites.json")
UTM = "EPSG:32644"
SITE_RADIUS_M = 400.0
INTENSITY_MMHR = 60.0
RUNOFF_C = 0.85
MANNING_N = 0.013
PONDING_WINDOW_S = 900

RNG = np.random.default_rng(42)
N_PERM = 20000

if not os.path.exists(SITES_JSON):
    print(f"Missing ground truth catalog: {SITES_JSON}")
    sys.exit(1)

with open(SITES_JSON, "r", encoding="utf-8") as f:
    _sites_doc = json.load(f)

KNOWN_FLOOD_SITES = {
    s["name"]: dict(
        lon=s["lon"],
        lat=s["lat"],
        in_zone=False,
        severity=s["severity"],
        source=f"{s['source_date']} {s['source_url'][:34]}...",
    )
    for s in _sites_doc["sites"]
    if s.get("is_strong", True)
}

verdicts = []


def report(name, ok, detail):
    tag = "OK  " if ok else "WEAK"
    print(f"  [{tag}] {name}: {detail}")
    verdicts.append((name, ok, detail))


print(f"ZONE: {ZONE}   ({SPATIAL_DIR})")
graphml_path = os.path.join(SPATIAL_DIR, "drainage_graph.graphml")
json_path = os.path.join(SPATIAL_DIR, "drainage_graph.json")

if not os.path.exists(graphml_path):
    print(f"GraphML network not found at {graphml_path}. Run network_graph_builder.py first.")
    sys.exit(1)

G = nx.read_graphml(graphml_path)
meta = json.load(open(json_path, encoding="utf-8"))["meta"]
bbox = meta["bbox"]

nodes = list(G.nodes)
lon = np.array([float(G.nodes[n]["lon"]) for n in nodes])
lat = np.array([float(G.nodes[n]["lat"]) for n in nodes])
x = np.array([float(G.nodes[n].get("x_utm", G.nodes[n].get("x", 0.0))) for n in nodes])
y = np.array([float(G.nodes[n].get("y_utm", G.nodes[n].get("y", 0.0))) for n in nodes])
terrain = np.array([float(G.nodes[n]["terrain_m"]) for n in nodes])
up_area = np.array([float(G.nodes[n]["upstream_area_m2"]) for n in nodes])
cap = np.array([float(G.nodes[n]["outgoing_capacity_m3s"]) for n in nodes])
is_min = np.array([bool(G.nodes[n]["is_local_min"]) for n in nodes])
is_out = np.array([bool(G.nodes[n]["is_outlet"]) for n in nodes])

q_in = RUNOFF_C * (INTENSITY_MMHR / 3.6e6) * up_area
excess = np.clip(q_in - cap, 0.0, None)

backup_cm = np.divide(excess * PONDING_WINDOW_S, up_area, out=np.zeros_like(excess), where=up_area > 0) * 100.0
backup_cm[is_out] = 0.0
stress = np.divide(q_in, cap, out=np.zeros_like(q_in), where=cap > 0)


def _cap_pipe(d_mm, s):
    d = d_mm / 1000.0
    return (1.0 / MANNING_N) * (math.pi * d * d / 4) * (d / 4) ** (2 / 3) * math.sqrt(max(s, 1e-6))


gap_of = {}
for u, v, d in G.edges(data=True):
    base = _cap_pipe(float(d.get("base_diameter_mm", 600.0)), float(d.get("slope", 0.001)))
    gap_of[u] = float(d.get("design_flow_m3s", 0.5)) / max(base, 1e-9)
provision_gap = np.array([gap_of.get(n, 0.0) for n in nodes])

print("=" * 68)
print(f"Predicted risk at {INTENSITY_MMHR:.0f} mm/hr (C={RUNOFF_C}, {PONDING_WINDOW_S//60} min window)")
print("=" * 68)
print(f"  manholes scored          : {len(nodes)}")
print(f"  conveyance backup max    : {backup_cm.max():.3f} cm")
print(f"  overloaded nodes         : {int((stress > 1).sum())} ({100*(stress > 1).mean():.0f}%)")
print(f"  provision gap spread     : {np.percentile(provision_gap, 99)/max(np.median(provision_gap), 1e-9):.0f}x")
print(f"  local minima count       : {int(is_min.sum())} ({100*is_min.mean():.1f}%)")

interior = ~is_out
depth_cm = provision_gap

# Tier 2: Validation against ground truth locations
to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)


def site_score(slon, slat):
    sx, sy = to_utm.transform(slon, slat)
    d = np.hypot(x - sx, y - sy)
    sel = d <= SITE_RADIUS_M
    if not sel.any():
        return None, 0
    return float(depth_cm[sel].max()), int(sel.sum())


for _k, _v in KNOWN_FLOOD_SITES.items():
    _sx, _sy = to_utm.transform(_v["lon"], _v["lat"])
    _v["n_nodes"] = int((np.hypot(x - _sx, y - _sy) <= SITE_RADIUS_M).sum())
    _v["in_zone"] = bool(
        bbox[0] <= _v["lon"] <= bbox[2]
        and bbox[1] <= _v["lat"] <= bbox[3]
        and _v["n_nodes"] > 0
    )

testable = {k: v for k, v in KNOWN_FLOOD_SITES.items() if v["in_zone"]}
print(f"\nTestable flood sites inside {ZONE} zone: {len(testable)}")

ref = np.empty(len(nodes))
for i in range(len(nodes)):
    d = np.hypot(x - x[i], y - y[i])
    ref[i] = depth_cm[d <= SITE_RADIUS_M].max()

obs_pct = []
site_names = []
for name, s in testable.items():
    sc, cnt = site_score(s["lon"], s["lat"])
    if sc is None:
        continue
    pct = float((ref < sc).mean())
    obs_pct.append(pct)
    site_names.append(name)
    print(f"  {name:<24} gap: {sc:>7.2f}x  percentile: {100*pct:>4.0f}% ({cnt} nodes)")

if obs_pct:
    obs_pct = np.array(obs_pct)
    mean_pct = obs_pct.mean()
    perm = np.array([RNG.choice(ref, size=len(obs_pct), replace=False) for _ in range(N_PERM)])
    perm_pct = np.array([[(ref < v).mean() for v in row] for row in perm]).mean(axis=1)
    p_val = float((perm_pct >= mean_pct).mean())

    print(f"\nMean percentile of known flood sites: {100*mean_pct:.1f}% (random chance: 50%)")
    print(f"Spatial permutation p-value: p = {p_val:.3f}")
    report("known sites rank above chance", mean_pct > 0.5, f"mean percentile {100*mean_pct:.0f}%")
print("Validation complete.")
