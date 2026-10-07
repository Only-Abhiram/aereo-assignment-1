import io
import zipfile

import pytest
from pyproj import Geod
from shapely.geometry import box

GEOD = Geod(ellps="WGS84")


def upload(client, name, data, **form):
    return client.post("/api/files/", files={"file": (name, data)}, data=form)


# ----------------------------------------------------------------------------- KML
def test_kml_upload_and_info(client, kml_bytes):
    r = upload(client, "survey.kml", kml_bytes)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "COMPLETED"
    assert body["feature_count"] == 4
    assert body["crs"] == "EPSG:4326"
    assert body["filename"] == "survey.kml"

    again = client.get(f"/api/files/{body['id']}/")
    assert again.status_code == 200 and again.json()["id"] == body["id"]


def test_kml_measurements_match_geodesic_truth(client, kml_bytes):
    fid = upload(client, "survey.kml", kml_bytes).json()["id"]
    data = client.get(f"/api/files/{fid}/measurements/").json()
    by_name = {f["geometry_type"]: f for f in data["features"]}

    poly = by_name["Polygon"]
    true_area, _ = GEOD.geometry_area_perimeter(box(78.40, 17.40, 78.41, 17.41))
    assert poly["status"] == "MEASURED"
    assert poly["projected_crs"] == "EPSG:32644"  # UTM 44N covers Hyderabad
    # UTM is conformal, not equal-area: expect up to ~0.2% scale error towards a zone edge.
    assert poly["measurements"]["area_m2"] == pytest.approx(abs(true_area), rel=3e-3)

    line = by_name["LineString"]
    true_len = GEOD.line_length([78.40, 78.41], [17.40, 17.40])
    assert line["measurements"]["length_m"] == pytest.approx(true_len, rel=1e-3)

    point = by_name["Point"]
    assert point["status"] == "NOT_APPLICABLE" and point["measurements"] == {}

    combo = by_name["GeometryCollection"]
    assert combo["status"] == "UNSUPPORTED"  # handled gracefully, not a crash

    assert data["summary"]["status_counts"]["MEASURED"] == 2


def test_features_endpoint_returns_geometry_crs_properties(client, kml_bytes):
    fid = upload(client, "survey.kml", kml_bytes).json()["id"]
    data = client.get(f"/api/files/{fid}/features/").json()
    first = data["features"][0]
    assert first["feature_id"] == 0
    assert first["geometry"]["type"] == "Polygon"
    assert first["crs"] == "EPSG:4326"
    assert first["properties"]["Name"] == "Plot A"


def test_pagination(client, kml_bytes):
    fid = upload(client, "survey.kml", kml_bytes).json()["id"]
    page = client.get(f"/api/files/{fid}/measurements/?limit=2&offset=1").json()
    assert [f["feature_id"] for f in page["features"]] == [1, 2]
    assert page["total"] == 4


# ----------------------------------------------------------------------------- Shapefile
def test_shapefile_wgs84(client, shapefile_zip):
    r = upload(client, "plots.zip", shapefile_zip())
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()
    expected, _ = GEOD.geometry_area_perimeter(box(78.40, 17.40, 78.41, 17.41))
    assert m["features"][0]["measurements"]["area_m2"] == pytest.approx(abs(expected), rel=3e-3)


def test_shapefile_already_projected_gives_same_answer(client, shapefile_zip):
    """The same square expressed in EPSG:32644 must measure the same as in EPSG:4326."""
    import geopandas as gpd

    sq = gpd.GeoSeries([box(78.40, 17.40, 78.41, 17.41)], crs="EPSG:4326").to_crs("EPSG:32644").iloc[0]
    fid_a = upload(client, "a.zip", shapefile_zip(geoms=[sq], crs="EPSG:32644", name="a")).json()["id"]
    fid_b = upload(client, "b.zip", shapefile_zip(geoms=[box(78.40, 17.40, 78.41, 17.41)], name="b")).json()["id"]
    a = client.get(f"/api/files/{fid_a}/measurements/").json()["features"][0]["measurements"]["area_m2"]
    b = client.get(f"/api/files/{fid_b}/measurements/").json()["features"][0]["measurements"]["area_m2"]
    assert a == pytest.approx(b, rel=1e-6)
    assert client.get(f"/api/files/{fid_a}/").json()["crs"] == "EPSG:32644"


def test_southern_hemisphere_uses_south_utm(client, shapefile_zip):
    fid = upload(client, "s.zip", shapefile_zip(geoms=[box(151.20, -33.87, 151.21, -33.86)], name="s")).json()["id"]
    f = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]
    assert f["projected_crs"] == "EPSG:32756"


# ----------------------------------------------------------------------------- Errors
def test_unsupported_extension(client):
    r = upload(client, "data.geojson", b"{}")
    assert r.status_code == 415


def test_empty_file(client):
    assert upload(client, "x.kml", b"").status_code == 422


def test_corrupt_zip_marks_file_failed(client):
    r = upload(client, "bad.zip", b"not a zip")
    assert r.status_code == 422
    fid = r.json()["id"]
    info = client.get(f"/api/files/{fid}/").json()
    assert info["status"] == "FAILED" and info["error"]
    assert client.get(f"/api/files/{fid}/measurements/").status_code == 409


def test_zip_without_shp(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hi")
    r = upload(client, "empty.zip", buf.getvalue())
    assert r.status_code == 422 and "No .shp" in r.json()["detail"]


def test_zip_slip_is_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.shp", "x")
    r = upload(client, "slip.zip", buf.getvalue())
    assert r.status_code == 422 and "unsafe" in r.json()["detail"]


def test_missing_prj_requires_default_crs(client, shapefile_zip, tmp_path):
    import zipfile as zf

    raw = shapefile_zip(name="noprj")
    src = zf.ZipFile(io.BytesIO(raw))
    buf = io.BytesIO()
    with zf.ZipFile(buf, "w") as out:
        for n in src.namelist():
            if not n.endswith(".prj"):
                out.writestr(n, src.read(n))
    r = upload(client, "noprj.zip", buf.getvalue())
    assert r.status_code == 422 and "default_crs" in r.json()["detail"]

    ok = upload(client, "noprj.zip", buf.getvalue(), default_crs="EPSG:4326")
    assert ok.status_code == 201 and ok.json()["crs"] == "EPSG:4326"


def test_unknown_id_404(client):
    assert client.get("/api/files/nope/").status_code == 404
    assert client.get("/api/files/nope/measurements/").status_code == 404


def test_upload_too_large(client):
    r = upload(client, "big.kml", b"0" * (6 * 1024 * 1024))
    assert r.status_code == 413
