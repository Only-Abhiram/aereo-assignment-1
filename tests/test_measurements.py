import pytest
from pyproj import CRS
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, box

from app.services.crs import utm_crs_for
from app.services.measurements import INVALID, MEASURED, NOT_APPLICABLE, UNSUPPORTED, measure_geometry

WGS84 = CRS.from_epsg(4326)


@pytest.mark.parametrize(
    "lon,lat,epsg",
    [(78.4, 17.4, 32644), (-0.1, 51.5, 32630), (151.2, -33.9, 32756), (-179.9, 10, 32601), (0, 89, 32661), (0, -85, 32761)],
)
def test_utm_selection(lon, lat, epsg):
    assert utm_crs_for(lon, lat).to_epsg() == epsg


def test_degrees_are_not_used_directly():
    res = measure_geometry(box(78.4, 17.4, 78.41, 17.41), WGS84)
    # 0.01° x 0.01° square ≈ 1.06 km x 1.10 km ≈ 1.17 km², never 0.0001
    assert 1_100_000 < res.measurements["area_m2"] < 1_250_000


def test_multipolygon_sums_parts():
    mp = MultiPolygon([box(78.4, 17.4, 78.41, 17.41), box(78.5, 17.4, 78.51, 17.41)])
    single = measure_geometry(box(78.4, 17.4, 78.41, 17.41), WGS84).measurements["area_m2"]
    assert measure_geometry(mp, WGS84).measurements["area_m2"] == pytest.approx(2 * single, rel=1e-3)


def test_bowtie_polygon_is_repaired():
    bowtie = Polygon([(78.4, 17.4), (78.41, 17.41), (78.41, 17.4), (78.4, 17.41)])
    res = measure_geometry(bowtie, WGS84)
    assert res.status == MEASURED and res.measurements["area_m2"] > 0 and "repaired" in res.message


def test_edge_cases_do_not_raise():
    assert measure_geometry(None, WGS84).status == INVALID
    assert measure_geometry(Polygon(), WGS84).status == INVALID
    assert measure_geometry(Point(1, 2), WGS84).status == NOT_APPLICABLE
    assert measure_geometry(GeometryCollection([Point(1, 2)]), WGS84).status == UNSUPPORTED
