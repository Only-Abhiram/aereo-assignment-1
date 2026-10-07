import io
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, box

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", max_upload_bytes=5 * 1024 * 1024,
                        max_unzipped_bytes=50 * 1024 * 1024)
    with TestClient(create_app(settings)) as c:
        yield c


def zip_dir(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in directory.iterdir():
            zf.write(p, p.name)
    return buf.getvalue()


@pytest.fixture()
def shapefile_zip(tmp_path):
    """Polygons in EPSG:4326 (near Hyderabad) and a line, written as a shapefile."""
    def make(crs="EPSG:4326", geoms=None, name="sample"):
        d = tmp_path / f"shp_{name}"
        d.mkdir()
        geoms = geoms or [box(78.40, 17.40, 78.41, 17.41), box(78.50, 17.50, 78.52, 17.51)]
        gdf = gpd.GeoDataFrame({"name": [f"f{i}" for i in range(len(geoms))]}, geometry=geoms, crs=crs)
        gdf.to_file(d / f"{name}.shp")
        return zip_dir(d)
    return make


KML_MIXED = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>Plot A</name><description>square</description><Polygon><outerBoundaryIs><LinearRing><coordinates>
    78.40,17.40,0 78.41,17.40,0 78.41,17.41,0 78.40,17.41,0 78.40,17.40,0
  </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
  <Placemark><name>Road</name><LineString><coordinates>78.40,17.40,0 78.41,17.40,0</coordinates></LineString></Placemark>
  <Placemark><name>Well</name><Point><coordinates>78.405,17.405,0</coordinates></Point></Placemark>
  <Placemark><name>Combo</name><MultiGeometry>
    <Point><coordinates>78.4,17.4,0</coordinates></Point>
    <LineString><coordinates>78.4,17.4,0 78.5,17.5,0</coordinates></LineString>
  </MultiGeometry></Placemark>
</Document></kml>
"""


@pytest.fixture()
def kml_bytes():
    return KML_MIXED.encode()
