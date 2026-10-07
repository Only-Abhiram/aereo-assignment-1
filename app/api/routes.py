from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse

from ..config import Settings
from ..db import Database
from ..errors import UnsupportedFileTypeError
from ..schemas import FeaturesResponse, FileInfo, MeasurementsResponse
from ..services.loader import detect_file_type
from ..services.processing import ProcessingFailed, process_upload

router = APIRouter(prefix="/api/files", tags=["files"])


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def _require_file(db: Database, file_id: str) -> dict:
    info = db.get_file(file_id)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File '{file_id}' not found.")
    return info


def _require_completed(info: dict) -> None:
    if info["status"] != "COMPLETED":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"File is {info['status']}" + (f": {info['error']}" if info["error"] else "."),
        )


@router.post(
    "/",
    response_model=FileInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and process a .zip (Shapefile) or .kml file",
    responses={415: {"description": "Unsupported file type"}, 413: {"description": "File too large"},
               422: {"description": "File could not be processed"}},
)
def upload_file(
    file: UploadFile = File(..., description="A .zip containing a Shapefile, or a .kml file"),
    default_crs: str | None = Form(None, description="CRS to assume if a shapefile has no .prj, e.g. EPSG:4326"),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
):
    filename = Path(file.filename or "upload").name
    try:
        detect_file_type(filename)  # fail fast, before reading the body
    except UnsupportedFileTypeError as exc:
        raise HTTPException(415, str(exc)) from exc

    with tempfile.TemporaryDirectory(prefix="upload_") as tmp:
        saved = Path(tmp) / filename  # real name keeps KML layer names meaningful
        size = 0
        with open(saved, "wb") as out:  # stream to disk with a hard size cap
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        413,
                        f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit.",
                    )
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "The uploaded file is empty.")

        try:
            file_id = process_upload(db, settings, saved, filename, default_crs)
        except ProcessingFailed as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": exc.message, "id": exc.file_id, "status": "FAILED"},
            )
    return db.get_file(file_id)


@router.get("/{file_id}/", response_model=FileInfo, summary="Get information about an uploaded file")
def get_file(file_id: str, db: Database = Depends(get_db)):
    return _require_file(db, file_id)


@router.get(
    "/{file_id}/features/",
    response_model=FeaturesResponse,
    summary="List extracted features (geometry, CRS, properties)",
)
def get_features(
    file_id: str,
    limit: int = Query(1000, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_db),
):
    info = _require_file(db, file_id)
    _require_completed(info)
    return {
        "file_id": file_id,
        "total": info["feature_count"],
        "limit": limit,
        "offset": offset,
        "features": db.get_features(file_id, limit, offset),
    }


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Per-feature measurements (area / length) plus file totals",
)
def get_measurements(
    file_id: str,
    limit: int = Query(1000, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_db),
):
    info = _require_file(db, file_id)
    _require_completed(info)
    items = []
    for f in db.get_features(file_id, limit, offset):
        items.append(
            {
                "feature_id": f["feature_id"],
                "layer": f["layer"],
                "geometry_type": f["geometry_type"],
                "crs": f["crs"],
                **f["measurement"],
            }
        )
    return {
        "file_id": file_id,
        "total": info["feature_count"],
        "limit": limit,
        "offset": offset,
        "summary": db.measurement_summary(file_id),
        "features": items,
    }
