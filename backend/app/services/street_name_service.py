"""Resolves node IDs to human-readable street names from network graph edges."""

from collections import Counter, defaultdict
import json
import os
from typing import Dict, List, Optional, Tuple

from backend.app.config import settings
from backend.app.services.routing_service import geographic_distance_m


class StreetNameService:
    """Loads and caches node-to-street-name mappings for supported zones."""

    GENERIC_NAMES = {
        "residential",
        "secondary",
        "tertiary",
        "primary",
        "living street",
        "unclassified",
        "service",
        "road",
        "path",
        "street",
        "lane",
        "unnamed",
        "none",
    }

    def __init__(self):
        self._cache: Dict[str, Dict[str, List[str]]] = {}
        self._named_nodes_cache: Dict[str, List[Tuple[str, float, float]]] = {}

    def get_street_names(self, zone: str) -> Dict[str, List[str]]:
        """Returns {node_id: [street_names]} for the requested zone."""
        zone_key = zone.lower()
        if zone_key in self._cache:
            return self._cache[zone_key]

        directory = (
            settings.SPATIAL_VALIDATION_DIR
            if zone_key == "validation"
            else settings.SPATIAL_PILOT_DIR
        )
        path = os.path.join(directory, "drainage_graph.json")
        incident: Dict[str, List[str]] = defaultdict(list)

        try:
            with open(path, "r", encoding="utf-8") as handle:
                graph = json.load(handle)
            for edge in graph.get("edges", []):
                name = str(edge.get("name", "") or "").strip()
                if not name or name.lower() in self.GENERIC_NAMES:
                    continue
                for endpoint in (edge.get("from"), edge.get("to")):
                    if endpoint is None:
                        continue
                    incident[str(endpoint)].append(name)
        except Exception:
            self._cache[zone_key] = {}
            self._named_nodes_cache[zone_key] = []
            return {}

        mapping: Dict[str, List[str]] = {}
        for node_id, names in incident.items():
            counts = Counter(names)
            ranked = sorted(set(names), key=lambda item: (-counts[item], -len(item), item))
            mapping[node_id] = ranked[:3]

        named_nodes: List[Tuple[str, float, float]] = []
        for node in graph.get("nodes", []):
            node_id = str(node.get("id", ""))
            names = mapping.get(node_id)
            if not names:
                continue
            try:
                node_lon = float(node["lon"])
                node_lat = float(node["lat"])
                named_nodes.append((names[0], node_lon, node_lat))
            except (KeyError, TypeError, ValueError):
                continue

        self._cache[zone_key] = mapping
        self._named_nodes_cache[zone_key] = named_nodes
        return mapping

    def nearest_named_street(
        self, zone: str, lon: float, lat: float, max_distance_m: float = 400.0
    ) -> Optional[Tuple[str, float]]:
        """Returns (street_name, distance_m) for the closest named street node."""
        zone_key = zone.lower()
        if zone_key not in self._cache or zone_key not in self._named_nodes_cache:
            self.get_street_names(zone)

        named_nodes = self._named_nodes_cache.get(zone_key, [])
        if not named_nodes:
            return None

        best_name: Optional[str] = None
        min_dist = float("inf")
        target_pt = (lon, lat)

        for name, n_lon, n_lat in named_nodes:
            d = geographic_distance_m(target_pt, (n_lon, n_lat))
            if d < min_dist:
                min_dist = d
                best_name = name

        if min_dist <= max_distance_m and best_name:
            return (best_name, min_dist)
        return None


street_name_service = StreetNameService()
