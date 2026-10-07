# Geospatial File Measurement API

A FastAPI backend that accepts a **zipped Shapefile** or a **KML** file, extracts every feature
(ID, geometry type, geometry, CRS, properties) and returns **area** (polygons) and **length**
(lines), computed in a projected, metric CRS, never on raw latitude/longitude degrees.

## Setup

Requires Python 3.10+ (developed on 3.12).

```bash
python -m venv .venv 

source .venv/Scripts/activate

pip install -r requirements-dev.txt

uvicorn app.main:app --reload
# Interactive docs:  http://127.0.0.1:8000/docs
```

Run the tests:

```bash
pytest
```

Interactive API explorer (optional): with the server running, `python test.py` opens a menu for every endpoint — pick a route, supply a file path or id when asked, and see the JSON response. Requires `requests` (`pip install requests` if it is not already installed).

Docker:

```bash
docker build -t geo-measure-api . && docker run -p 8000:8000 geo-measure-api
```

Configuration (environment variables, all optional):

| Variable | Default | Meaning |
|---|---|---|
| `GEO_DATA_DIR` | `data` | Folder holding the SQLite database |
| `GEO_MAX_UPLOAD_MB` | `50` | Max upload size (HTTP 413 beyond this) |
| `GEO_MAX_UNZIPPED_MB` | `500` | Max uncompressed zip size (zip-bomb guard) |

Sample inputs are in `samples/` (`survey.kml`, `plots_shapefile.zip`).

## API

### `POST /api/files/`: upload and process
Multipart form: `file` (required, `.zip` or `.kml`), `default_crs` (optional, e.g. `EPSG:4326`,
used only when a shapefile has no `.prj`).

```bash
curl -F "file=@samples/survey.kml" http://127.0.0.1:8000/api/files/
```
```json
{
  "id": "92f4f38ae55845d9b281fc415603fb89",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 4,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "geometry_types": {"Polygon": 1, "LineString": 1, "Point": 1, "GeometryCollection": 1},
  "error": null,
  "created_at": "2026-10-07T15:21:06+00:00",
  "completed_at": "2026-10-07T15:21:06+00:00"
}
```

| Status | Meaning |
|---|---|
| 201 | Processed successfully |
| 413 | Upload larger than the limit |
| 415 | Extension is not `.zip` / `.kml` |
| 422 | File could not be processed (corrupt zip, no `.shp`, missing `.prj`, empty...). Body: `{"detail": "...", "id": "...", "status": "FAILED"}`; the failed record can still be fetched |

### `GET /api/files/{id}/`: file information
Returns the same object as above. `404` if the id is unknown. `crs` is `"MIXED"` if a zip holds
shapefiles in different CRSs (each feature still carries its own `crs`).

### `GET /api/files/{id}/measurements/`: measurements
Query params: `limit` (default 1000, max 10000), `offset`. `409` if the file is `FAILED`.
`summary` totals cover the whole file, not just the requested page.

```json
{
  "file_id": "92f4f38a...",
  "total": 4, "limit": 1000, "offset": 0,
  "summary": {
    "total_area_m2": 1177227.6803,
    "total_length_m": 1063.1473,
    "status_counts": {"MEASURED": 2, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1}
  },
  "features": [
    {"feature_id": 0, "layer": "survey", "geometry_type": "Polygon", "crs": "EPSG:4326",
     "status": "MEASURED", "projected_crs": "EPSG:32644", "message": null,
     "measurements": {"area_m2": 1177227.6803, "area_hectares": 117.722768, "perimeter_m": 4340.9058}},
    {"feature_id": 1, "geometry_type": "LineString", "status": "MEASURED", "projected_crs": "EPSG:32644",
     "measurements": {"length_m": 1063.1473}, "...": "..."},
    {"feature_id": 2, "geometry_type": "Point", "status": "NOT_APPLICABLE", "measurements": {},
     "message": "No measurement is defined for points.", "...": "..."},
    {"feature_id": 3, "geometry_type": "GeometryCollection", "status": "UNSUPPORTED", "measurements": {},
     "message": "Measurements are not supported for geometry type 'GeometryCollection'.", "...": "..."}
  ]
}
```

Per-feature `status`: `MEASURED`, `NOT_APPLICABLE` (points), `UNSUPPORTED` (e.g. `GeometryCollection`),
`INVALID` (null/empty/un-projectable geometry). One bad feature never fails the request.

### `GET /api/files/{id}/features/`: extracted features (extra)
Same pagination. Returns `feature_id`, `layer`, `geometry_type`, `geometry` (GeoJSON, in the file's
own CRS), `crs`, `properties`.

## Architecture

```
app/
  main.py              app factory (wires settings + DB + router)
  config.py            env-driven settings
  api/routes.py        HTTP layer: validation, streaming upload, status codes
  schemas.py           Pydantic response models (drive /docs)
  db.py                SQLite repository (files, features)
  errors.py            domain exceptions -> mapped to HTTP codes in the router
  services/
    loader.py          read .kml / zipped .shp into plain Layer objects
    crs.py             CRS labels, cached transformers, UTM-zone selection
    measurements.py    per-geometry measurement
    processing.py      orchestrates load -> measure -> persist
tests/                 25 tests (unit + API)
```

**File-processing flow**
1. Router checks the extension (415 early), streams the body to a temp file with a size cap.
2. A `files` row is created (`PROCESSING`).
3. `loader` reads the file. KML: every layer/folder via GDAL (through `pyogrio`), CRS fixed to
   EPSG:4326 as the KML spec defines. Shapefile: the zip is extracted safely (rejects `..`/absolute
   paths, caps uncompressed size, extracts only shapefile sidecar extensions, ignores `__MACOSX`),
   every `.shp` is read, and the CRS comes from the `.prj`.
4. Each feature is measured, then all features and the `COMPLETED` status are written in **one
   transaction**, so a file is never half-stored. Errors flip the row to `FAILED` with a message.

**Measurement flow** (`measure_geometry`)
1. Null/empty → `INVALID`; points → `NOT_APPLICABLE`; anything other than (Multi)Polygon /
   (Multi)LineString → `UNSUPPORTED`.
2. Drop Z (KML altitudes are irrelevant to planar measurement).
3. Find the feature's centroid in WGS84, choose the UTM zone, project from the **source CRS directly**
   to that UTM CRS.
4. Polygon → `area_m2`, `area_hectares`, `perimeter_m`; line → `length_m`. Invalid polygons
   (e.g. bow-ties) are repaired with `make_valid` first and flagged in `message`.

**CRS handling**
- The source CRS is kept per feature and reported as e.g. `EPSG:4326`.
- Measurements are always done in metres in a projected CRS, even if the source is already projected
  (so feet-based or odd projections still return metres). The CRS used is returned as `projected_crs`.
- Projection choice is **per feature**: UTM zone from the centroid (EPSG:326xx north / 327xx south),
  UPS for latitudes beyond +84° / −80°. A file spanning several zones is therefore handled correctly.
- Transformers use `always_xy=True` to avoid lat/lon axis-order bugs and are cached.
- A shapefile with no `.prj` is rejected with a clear message unless `default_crs` is supplied.

## Design decisions

| Decision | Why | Alternatives considered |
|---|---|---|
| **FastAPI** | Typed models, automatic OpenAPI docs, small footprint | Django + DRF: heavier, ORM/admin not needed here |
| **GDAL via pyogrio/GeoPandas** for reading | One reader for both formats, handles encodings and KML folders | Hand-parsing KML with `lxml`/`fastkml`: more code and edge cases; `pyshp`: no CRS handling |
| **Per-feature UTM zone** | Metre-based, accurate (≲0.1–0.2% inside a zone), works for multi-zone files | One CRS per file (breaks on wide files); geodesic ellipsoidal math via `pyproj.Geod` (most accurate, but the brief asks for projected CRS) |
| **Synchronous processing** inside the request | Simple, deterministic; the response already contains the final status | Celery/RQ queue + polling: right for very large files, adds infrastructure. `status` is already modelled (`PROCESSING/COMPLETED/FAILED`) so this is a small change |
| **SQLite, results stored as JSON** | Zero setup, transactional, survives restarts, supports paginated reads | PostgreSQL/PostGIS: better for spatial queries and multi-instance deployments |
| **Original upload not kept** | Features and measurements are all that is needed; avoids storing user files | Object storage (S3) if re-processing is wanted |
| **Per-feature status instead of failing the request** | Real-world files contain bad geometries; users still get the rest | Fail-fast: simpler but unfriendly |
| **Geometry returned in the file's own CRS** | No lossy round trip; CRS is returned alongside | Always reprojecting to WGS84 for GeoJSON consumers |

Known accuracy note: UTM is conformal, not equal-area, so areas far from a zone's central meridian
can be off by roughly 0.1–0.2%. The tests compare against ellipsoidal truth (`pyproj.Geod`) with a
0.3% tolerance.

## Learnings

- Axis order is a classic trap: EPSG:4326 is lat/lon by definition, but GIS file formats store lon/lat.
  `always_xy=True` makes this explicit.
- UTM zone selection is trivial for one point and subtle for polygons: choosing per feature avoids
  wrong answers for files covering several zones.
- Untrusted zips need active defence (zip-slip, zip bombs, unexpected members).
- Invalid polygons silently give wrong areas; repairing and flagging beats failing or trusting them.
- Validating against an independent oracle (`pyproj.Geod`) caught the UTM scale error early and
  made the accuracy trade-off a documented decision instead of a surprise.

## Future scope

- Background processing (Celery/RQ) with progress reporting, and streaming/chunked reads for very large files.
- Equal-area projection (e.g. local Lambert Azimuthal Equal Area) for polygon areas, optionally with a
  geodesic cross-check value in the response.
- PostgreSQL + PostGIS, spatial queries (bbox filter), and a GeoJSON `FeatureCollection` output.
- More formats: KMZ, GeoJSON, GeoPackage, multi-layer selection.
- Antimeridian-crossing geometries; 3D/slope-aware length using KML altitudes.
- Authentication, per-user file ownership, rate limiting, file retention/cleanup.
- Structured logging, metrics, CI (GitHub Actions: pytest + ruff + mypy).
