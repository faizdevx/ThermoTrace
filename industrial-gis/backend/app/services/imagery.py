"""Satellite imagery verification for a thermal source: thin API wrapper around ``src.imagery.core``.

The worker pre-computes the analysis for alerts and industrial candidates and writes it to a shared on-disk cache
(``THERMAL_IMAGERY_DIR``, mounted read-only into the API); a request for any other source computes it on demand
(~20-30 s: STAC search, two scenes, Landsat) and keeps it in memory. Nothing is invented: no scene -> "not available".
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:                                        # Docker image: src/imagery is copied next to the backend
    from src.imagery import core
except ImportError:                         # development checkout: industrial-data sits next to industrial-gis
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "industrial-data"))
    from src.imagery import core

# names the earlier single-scene implementation exported (tests and callers use them)
MAX_CLOUD = core.MAX_CLOUD
WINDOW_DAYS = core.WINDOW_DAYS
TRUE_BANDS = core.TRUE_BANDS
SWIR_BANDS = core.SWIR_BANDS
pick_scenes = core.pick_after


def chip_for(source_id: str, lon: float, lat: float, last_seen: str, first_seen: str | None = None) -> dict[str, Any]:
    """Disk cache -> memory -> compute. Raises only on STAC / COG service failure."""
    return core.cached_analysis(source_id, lon, lat, last_seen, first_seen=first_seen)
