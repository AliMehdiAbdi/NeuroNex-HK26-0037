"""LRU-cached reader for nodes and edges GeoJSON FeatureCollections."""

import json
import os
from typing import Any, Dict

from backend.app.config import settings
from backend.app.core.cache import LRUCache
from backend.app.core.exceptions import AssetNotFoundException
from backend.app.services.file_index import file_index


class GeoJsonLoader:
    """Load and cache GeoJSON layers from disk."""

    def __init__(self):
        self._cache = LRUCache(maxsize=settings.GEOJSON_CACHE_MAXSIZE)

    def load_layer(self, zone: str, scenario_slug: str, timestep: float, layer_type: str) -> Dict[str, Any]:
        """Loads nodes or edges GeoJSON from disk or LRU cache."""
        filepath = file_index.get_geojson_path(zone, scenario_slug, timestep, layer_type)
        cache_key = filepath

        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if not os.path.exists(filepath):
            raise AssetNotFoundException(os.path.basename(filepath))

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._cache.set(cache_key, data)
        return data

    def get_cache_stats(self) -> Dict[str, Any]:
        return self._cache.stats()


geojson_loader = GeoJsonLoader()
