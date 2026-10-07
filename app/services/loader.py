"""Reading geospatial files (zipped Shapefile, KML) into plain feature records."""
from __future__ import annotations

import datetime as dt
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import numpy as np
import pandas as pd
import pyogrio
from pyproj import CRS
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from ..errors import InvalidGeoFileError, UnsupportedFileTypeError
from .crs import WGS84

ACCEPTED_EXTENSIONS = {".zip": "shapefile", ".kml": "kml"}
SHAPEFILE_SIDECARS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}


@dataclass
class Layer:
    """A group of features sharing one CRS (one KML layer or one .shp)."""

    name: str
    crs: CRS
    geometries: list[BaseGeometry | None]
    properties: list[dict[str, Any]]


def detect_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ACCEPTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{ext or filename}'. Upload a .zip (Shapefile) or a .kml file."
        )
    return ACCEPTED_EXTENSIONS[ext]


# --------------------------------------------------------------------------- helpers
def _jsonable(value: Any) -> Any:
    """Convert pandas/numpy scalars to JSON-safe Python values (NaN/NaT -> None)."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (np.generic,)):
        value = value.item()
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _frame_to_layer(name: str, frame, crs: CRS) -> Layer:
    geom_col = frame.geometry.name
    attrs = frame.drop(columns=[geom_col])
    props = []
    for record in attrs.to_dict(orient="records"):
        cleaned = {k: _jsonable(v) for k, v in record.items()}
        props.append({k: v for k, v in cleaned.items() if v not in (None, "")})
    return Layer(name, crs, list(frame.geometry.values), props)


def geometry_to_geojson(geom: BaseGeometry | None) -> dict | None:
    return None if geom is None else json.loads(json.dumps(mapping(geom)))


# --------------------------------------------------------------------------- KML
def _read_kml(path: Path) -> list[Layer]:
    try:
        layers = pyogrio.list_layers(path)
    except Exception as exc:  # noqa: BLE001
        raise InvalidGeoFileError(f"Could not read KML file: {exc}") from exc

    out: list[Layer] = []
    for layer_name, _ in layers:
        try:
            frame = pyogrio.read_dataframe(path, layer=layer_name)
        except Exception as exc:  # noqa: BLE001
            raise InvalidGeoFileError(f"Could not read KML layer '{layer_name}': {exc}") from exc
        if len(frame):
            # KML is defined to always use WGS84 longitude/latitude.
            out.append(_frame_to_layer(layer_name, frame, WGS84))
    if not out:
        raise InvalidGeoFileError("The KML file contains no features.")
    return out


# --------------------------------------------------------------------------- Shapefile
def _safe_extract(zip_path: Path, dest: Path, max_unzipped: int) -> list[Path]:
    """Extract only shapefile-related members, defending against zip-slip and zip bombs."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise InvalidGeoFileError("The uploaded file is not a valid zip archive.") from exc

    with archive:
        members = [i for i in archive.infolist() if not i.is_dir()]
        if sum(i.file_size for i in members) > max_unzipped:
            raise InvalidGeoFileError("The archive is too large once uncompressed.")
        extracted: list[Path] = []
        for info in members:
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts
            if "__MACOSX" in parts or parts[-1].startswith("."):
                continue
            if PurePosixPath(*parts).is_absolute() or ".." in parts:
                raise InvalidGeoFileError("The archive contains unsafe file paths.")
            if Path(parts[-1]).suffix.lower() not in SHAPEFILE_SIDECARS:
                continue
            # Keep the folder layout so .shp/.shx/.dbf/.prj siblings stay together
            # (".." and absolute paths were rejected above, so this cannot escape `dest`).
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            extracted.append(target)
        return extracted


def _read_shapefiles(zip_path: Path, workdir: Path, max_unzipped: int, default_crs: str | None) -> list[Layer]:
    files = _safe_extract(zip_path, workdir, max_unzipped)
    shp_files = [f for f in files if f.suffix.lower() == ".shp"]
    if not shp_files:
        raise InvalidGeoFileError("No .shp file was found inside the zip archive.")

    layers: list[Layer] = []
    for shp in shp_files:
        display_name = shp.stem
        siblings = {f.suffix.lower() for f in files if f.parent == shp.parent and f.stem == shp.stem}
        if not {".shx", ".dbf"} <= siblings:
            # GDAL can sometimes rebuild .shx, but a missing .dbf means lost attributes.
            missing = sorted({".shx", ".dbf"} - siblings)
            raise InvalidGeoFileError(f"Shapefile '{display_name}' is missing: {', '.join(missing)}.")
        try:
            frame = pyogrio.read_dataframe(shp)
        except Exception as exc:  # noqa: BLE001
            raise InvalidGeoFileError(f"Could not read shapefile '{display_name}': {exc}") from exc

        crs = frame.crs
        if crs is None:
            if not default_crs:
                raise InvalidGeoFileError(
                    f"Shapefile '{display_name}' has no .prj file so its CRS is unknown. "
                    "Include the .prj or pass the `default_crs` form field (e.g. EPSG:4326)."
                )
            try:
                crs = CRS.from_user_input(default_crs)
            except Exception as exc:  # noqa: BLE001
                raise InvalidGeoFileError(f"Invalid default_crs '{default_crs}'.") from exc
        else:
            crs = CRS.from_user_input(crs)
        if len(frame):
            layers.append(_frame_to_layer(display_name, frame, crs))
    if not layers:
        raise InvalidGeoFileError("The shapefile contains no features.")
    return layers


# --------------------------------------------------------------------------- entrypoint
def load_layers(
    path: Path, file_type: str, workdir: Path, max_unzipped: int, default_crs: str | None = None
) -> list[Layer]:
    if file_type == "kml":
        return _read_kml(path)
    return _read_shapefiles(path, workdir, max_unzipped, default_crs)


def iter_features(layers: list[Layer]) -> Iterator[tuple[int, Layer, BaseGeometry | None, dict]]:
    """Yield ``(global_index, layer, geometry, properties)``."""
    index = 0
    for layer in layers:
        for geom, props in zip(layer.geometries, layer.properties):
            yield index, layer, geom, props
            index += 1
