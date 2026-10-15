"""LRU-cached file reader for time-series scenario timelines."""

import json
import os
from typing import Any, Dict

from backend.app.config import settings
from backend.app.core.cache import LRUCache
from backend.app.core.exceptions import AssetNotFoundException
from backend.app.services.file_index import file_index


class TimelineLoader:
    """Loads and caches pre-computed scenario timeline JSON files."""

    def __init__(self):
        self._cache = LRUCache(maxsize=settings.TIMELINE_CACHE_MAXSIZE)

    def load_timeline(self, zone: str, scenario_slug: str) -> Dict[str, Any]:
        """Loads scenario timeline from disk or LRU cache."""
        filepath = file_index.get_timeline_path(zone, scenario_slug)
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


timeline_loader = TimelineLoader()
