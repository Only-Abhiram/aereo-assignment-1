"""CRS helpers: labelling, transformer caching and projected-CRS selection."""
from __future__ import annotations

import math
from functools import lru_cache

from pyproj import CRS, Transformer

WGS84 = CRS.from_epsg(4326)


def crs_label(crs: CRS | None) -> str | None:
    """Short human-readable identifier, e.g. ``EPSG:4326``."""
    if crs is None:
        return None
    epsg = crs.to_epsg(min_confidence=70)
    return f"EPSG:{epsg}" if epsg else (crs.name or "UNKNOWN")


@lru_cache(maxsize=256)
def get_transformer(src: CRS, dst: CRS) -> Transformer:
    # always_xy=True -> (lon, lat) / (x, y) order regardless of the CRS axis definition.
    return Transformer.from_crs(src, dst, always_xy=True)


def utm_crs_for(lon: float, lat: float) -> CRS:
    """Pick the projected CRS used to measure a geometry located at (lon, lat).

    * -80 <= lat <= 84 -> the WGS84 / UTM zone containing the point (EPSG:326xx north,
      EPSG:327xx south). UTM keeps scale distortion below ~0.1% inside a zone.
    * beyond that      -> WGS84 / UPS North (EPSG:32661) or South (EPSG:32761), because
      UTM is not defined at the poles.
    """
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise ValueError("Cannot choose a projected CRS for a non-finite location")
    if lat > 84:
        return CRS.from_epsg(32661)
    if lat < -80:
        return CRS.from_epsg(32761)
    lon = ((lon + 180) % 360) - 180  # normalise to [-180, 180)
    zone = int((lon + 180) // 6) + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)
