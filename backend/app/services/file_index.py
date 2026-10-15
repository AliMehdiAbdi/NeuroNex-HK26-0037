"""Catalog and asset indexer for simulation scenarios, manifests, and GeoJSON outputs."""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.app.config import settings
from backend.app.core.exceptions import ScenarioNotFoundException, ZoneNotFoundException


class FileIndexService:
    """Discovers, indexes, and resolves nowcast assets from data and outputs directories."""

    def __init__(self):
        self._geojson_files: Dict[Tuple[str, str, int, str], str] = {}
        self._timeline_files: Dict[Tuple[str, str], str] = {}
        self._summary_manifests: Dict[str, Dict[str, Any]] = {}
        self._scenario_catalog: Dict[str, Dict[str, Any]] = {}
        self._slug_map: Dict[str, str] = {}
        self.total_geojson_count = 0
        self.reindex()

    def reindex(self) -> None:
        """Scan catalog and outputs folders to rebuild lookup indices."""
        self._geojson_files.clear()
        self._timeline_files.clear()
        self._summary_manifests.clear()
        self._slug_map.clear()
        self.total_geojson_count = 0

        # Load scenario definitions from catalog
        storms_json_path = os.path.join(settings.CATALOGS_DIR, "historical_storms.json")
        if os.path.exists(storms_json_path):
            with open(storms_json_path, "r", encoding="utf-8") as f:
                storms_doc = json.load(f)
                for s in storms_doc.get("scenarios", []):
                    sid = s["id"]
                    slug = self._make_slug(sid)
                    self._scenario_catalog[sid] = s
                    self._register_aliases(sid, slug, s.get("name", ""))

        # Load engine summary manifests per zone
        for zone in settings.SUPPORTED_ZONES:
            manifest_path = os.path.join(settings.OUTPUTS_DIR, f"engine_summary_{zone}.json")
            if os.path.exists(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as f:
                    self._summary_manifests[zone] = json.load(f)
                    for sc in self._summary_manifests[zone].get("exported_scenarios", []):
                        sid = sc["id"]
                        slug = self._make_slug(sid)
                        self._register_aliases(sid, slug, sc.get("name", ""))

        # Scan precomputed outputs for GeoJSON layers and timeline JSON files
        if os.path.exists(settings.OUTPUTS_DIR):
            for fname in os.listdir(settings.OUTPUTS_DIR):
                fpath = os.path.join(settings.OUTPUTS_DIR, fname)
                if not os.path.isfile(fpath):
                    continue

                if fname.startswith("nowcast_") and fname.endswith(".geojson"):
                    m = re.match(r"^nowcast_([a-zA-Z0-9]+)_(.+)_t(\d+)_(nodes|edges)\.geojson$", fname)
                    if m:
                        zone, slug, t_str, layer_type = m.groups()
                        t_int = int(t_str)
                        self._geojson_files[(zone.lower(), slug.lower(), t_int, layer_type.lower())] = fpath
                        self.total_geojson_count += 1
                        self._register_aliases(slug, slug)

                elif fname.startswith("timeline_") and fname.endswith(".json"):
                    m = re.match(r"^timeline_([a-zA-Z0-9]+)_(.+)\.json$", fname)
                    if m:
                        zone, slug = m.groups()
                        self._timeline_files[(zone.lower(), slug.lower())] = fpath
                        self._register_aliases(slug, slug)

    def _make_slug(self, text: str) -> str:
        return text.replace(" ", "_").replace("-", "_").lower()

    def _register_aliases(self, raw_id: str, slug: str, name: str = "") -> None:
        clean_slug = self._make_slug(slug)
        self._slug_map[raw_id.lower()] = clean_slug
        self._slug_map[clean_slug] = clean_slug
        self._slug_map[raw_id.upper()] = clean_slug
        self._slug_map[raw_id] = clean_slug
        if name:
            self._slug_map[name.lower()] = clean_slug

        short_mappings = {
            "cpheeo_60mm": "cpheeo_extreme_60mm",
            "cpheeo-60mm": "cpheeo_extreme_60mm",
            "60mm": "cpheeo_extreme_60mm",
            "cpheeo_15mm": "cpheeo_design_15mm",
            "15mm": "cpheeo_design_15mm",
            "cpheeo_30mm": "cpheeo_stress_30mm",
            "30mm": "cpheeo_stress_30mm",
            "cpheeo_100mm": "cpheeo_cloudburst_100mm",
            "100mm": "cpheeo_cloudburst_100mm",
            "imd_2023": "imd_2023_09_14_extreme",
            "2023": "imd_2023_09_14_extreme",
            "imd_2026": "imd_2026_08_18_monsoon",
            "2026": "imd_2026_08_18_monsoon",
            "imd_2021": "imd_2021_07_28_flash",
            "2021": "imd_2021_07_28_flash",
        }
        for k, v in short_mappings.items():
            self._slug_map[k] = v

    def resolve_slug(self, scenario_query: str) -> str:
        """Resolve user input (ID, slug, or short name) into canonical scenario slug."""
        clean = scenario_query.strip().lower()
        if clean in self._slug_map:
            return self._slug_map[clean]

        slug_candidate = self._make_slug(scenario_query)
        if slug_candidate in self._slug_map:
            return self._slug_map[slug_candidate]

        for k, v in self._slug_map.items():
            if clean in k or k in clean:
                return v

        raise ScenarioNotFoundException(scenario_query)

    def validate_zone(self, zone: str) -> str:
        """Validate and normalize zone identifiers."""
        z = zone.strip().lower()
        if z in ["pilot", "pilot_cop", "pilot_srtm", "central", "core"]:
            return "pilot"
        elif z in ["validation", "validation_cop", "validation_srtm", "val", "trans_gomti", "north"]:
            return "validation"
        elif z in settings.SUPPORTED_ZONES:
            return z
        raise ZoneNotFoundException(zone)

    def get_geojson_path(self, zone: str, scenario_slug: str, timestep: float, layer_type: str) -> str:
        """Get file path for a requested GeoJSON layer."""
        z = self.validate_zone(zone)
        slug = self.resolve_slug(scenario_slug)

        avail_times = settings.STANDARD_TIMESTEPS_MIN
        snapped_t = min(avail_times, key=lambda x: abs(x - timestep))
        t_int = int(snapped_t)

        key = (z, slug, t_int, layer_type.lower())
        path = self._geojson_files.get(key)
        if path and os.path.exists(path):
            return path

        fallback_filename = f"nowcast_{z}_{slug}_t{t_int}_{layer_type.lower()}.geojson"
        return os.path.join(settings.OUTPUTS_DIR, fallback_filename)

    def get_timeline_path(self, zone: str, scenario_slug: str) -> str:
        """Get file path for a scenario timeline JSON."""
        z = self.validate_zone(zone)
        slug = self.resolve_slug(scenario_slug)

        key = (z, slug)
        path = self._timeline_files.get(key)
        if path and os.path.exists(path):
            return path

        fallback_filename = f"timeline_{z}_{slug}.json"
        return os.path.join(settings.OUTPUTS_DIR, fallback_filename)

    def get_scenarios_for_zone(self, zone: str = "pilot") -> List[Dict[str, Any]]:
        """Return scenario list with peak hydraulic metrics."""
        z = self.validate_zone(zone)
        manifest = self._summary_manifests.get(z)

        if manifest and "exported_scenarios" in manifest:
            items = []
            for sc in manifest["exported_scenarios"]:
                sid = sc["id"]
                slug = self._make_slug(sid)
                cat_info = self._scenario_catalog.get(sid, {})
                items.append({
                    "id": sid,
                    "slug": slug,
                    "name": sc.get("name", cat_info.get("name", sid)),
                    "category": sc.get("category", cat_info.get("category", "General")),
                    "peak_intensity_mmhr": float(sc.get("peak_intensity_mmhr", 0.0)),
                    "total_depth_mm": float(sc.get("total_depth_mm", 0.0)),
                    "duration_hours": float(cat_info.get("duration_hours", 2.0)),
                    "description": cat_info.get("description", ""),
                    "news_reference": cat_info.get("news_reference", ""),
                    "timeline_file": sc.get("timeline_file", f"timeline_{z}_{slug}.json"),
                    "peak_max_depth_cm": sc.get("peak_max_depth_cm", 0.0),
                    "peak_risk_category": sc.get("peak_risk_category", "LOW"),
                    "peak_impassable_km": sc.get("peak_impassable_km", 0.0),
                })
            return items

        items = []
        for sid, cat_info in self._scenario_catalog.items():
            slug = self._make_slug(sid)
            items.append({
                "id": sid,
                "slug": slug,
                "name": cat_info.get("name", sid),
                "category": cat_info.get("category", "General"),
                "peak_intensity_mmhr": float(cat_info.get("peak_intensity_mmhr", 0.0)),
                "total_depth_mm": float(cat_info.get("total_depth_mm", 0.0)),
                "duration_hours": float(cat_info.get("duration_hours", 2.0)),
                "description": cat_info.get("description", ""),
                "news_reference": cat_info.get("news_reference", ""),
                "timeline_file": f"timeline_{z}_{slug}.json",
            })
        return items


file_index = FileIndexService()
