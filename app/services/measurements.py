"""Measurement calculation for a single geometry.

Flow:  source CRS -> (WGS84 centroid) -> pick UTM zone -> project -> measure in metres.
Distances/areas are *never* computed on raw lon/lat degrees.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from pyproj import CRS
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

from .crs import WGS84, crs_label, get_transformer, utm_crs_for

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString"}
POINT_LIKE = {"Point", "MultiPoint"}

# Measurement status values
MEASURED = "MEASURED"
NOT_APPLICABLE = "NOT_APPLICABLE"  # valid geometry, but no measurement is defined (points)
UNSUPPORTED = "UNSUPPORTED"  # geometry type we do not measure (e.g. GeometryCollection)
INVALID = "INVALID"  # null / empty / un-projectable geometry


@dataclass
class MeasurementResult:
    status: str
    measurements: dict[str, float] = field(default_factory=dict)
    projected_crs: str | None = None
    message: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "measurements": self.measurements,
            "projected_crs": self.projected_crs,
            "message": self.message,
        }


def _project(geom: BaseGeometry, transformer) -> BaseGeometry:
    """Vectorised coordinate transform (x, y) -> (x', y')."""
    return shapely.transform(geom, lambda c: np.column_stack(transformer.transform(c[:, 0], c[:, 1])))


def _r(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def measure_geometry(geom: BaseGeometry | None, src_crs: CRS) -> MeasurementResult:
    """Measure ``geom`` (expressed in ``src_crs``). Never raises for bad data."""
    if geom is None or geom.is_empty:
        return MeasurementResult(INVALID, message="Feature has no (or an empty) geometry.")

    gtype = geom.geom_type
    if gtype in POINT_LIKE:
        return MeasurementResult(NOT_APPLICABLE, message="No measurement is defined for points.")
    if gtype not in POLYGONAL | LINEAR:
        return MeasurementResult(
            UNSUPPORTED, message=f"Measurements are not supported for geometry type '{gtype}'."
        )

    try:
        geom = shapely.force_2d(geom)  # KML often carries an altitude; it is irrelevant here

        # 1) Locate the geometry on the globe to choose a suitable projection.
        if src_crs == WGS84:
            geom_wgs84 = geom
        else:
            geom_wgs84 = _project(geom, get_transformer(src_crs, WGS84))
        centroid = geom_wgs84.centroid
        utm = utm_crs_for(centroid.x, centroid.y)

        # 2) Project straight from the source CRS to the metric CRS.
        projected = _project(geom, get_transformer(src_crs, utm))
        minx, miny, maxx, maxy = projected.bounds
        if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
            return MeasurementResult(
                INVALID, message="Geometry could not be projected (coordinates out of range)."
            )

        message = None
        label = crs_label(utm)

        if gtype in POLYGONAL:
            if not projected.is_valid:
                # Self-intersecting rings etc. give meaningless areas; repair first.
                repaired = make_valid(projected)
                polys = [p for p in shapely.get_parts(repaired) if p.geom_type == "Polygon"]
                area = sum(p.area for p in polys)
                perimeter = sum(p.length for p in polys)
                message = "Geometry was invalid and was repaired (make_valid) before measuring."
            else:
                area, perimeter = projected.area, projected.length
            return MeasurementResult(
                MEASURED,
                {
                    "area_m2": _r(area),
                    "area_hectares": _r(area / 10_000, 6),
                    "perimeter_m": _r(perimeter),
                },
                label,
                message,
            )

        return MeasurementResult(MEASURED, {"length_m": _r(projected.length)}, label)

    except Exception as exc:  # noqa: BLE001 - one bad feature must not fail the whole file
        return MeasurementResult(INVALID, message=f"Measurement failed: {exc}")
