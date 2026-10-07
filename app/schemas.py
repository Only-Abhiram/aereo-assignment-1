"""Pydantic response models (also drive the OpenAPI docs at /docs)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FileStatus = Literal["PROCESSING", "COMPLETED", "FAILED"]
MeasurementStatus = Literal["MEASURED", "NOT_APPLICABLE", "UNSUPPORTED", "INVALID"]


class FileInfo(BaseModel):
    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None = Field(None, description="CRS of the file, or 'MIXED' if layers differ")
    status: FileStatus
    geometry_types: dict[str, int] = {}
    error: str | None = None
    created_at: str
    completed_at: str | None = None


class MeasurementOut(BaseModel):
    status: MeasurementStatus
    measurements: dict[str, float] = {}
    projected_crs: str | None = Field(None, description="Metric CRS used for the calculation")
    message: str | None = None


class FeatureOut(BaseModel):
    feature_id: int
    layer: str | None = None
    geometry_type: str | None = None
    geometry: dict[str, Any] | None = Field(None, description="GeoJSON geometry in the file's CRS")
    crs: str | None = None
    properties: dict[str, Any] = {}


class FeaturesResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    features: list[FeatureOut]


class FeatureMeasurement(BaseModel):
    feature_id: int
    layer: str | None = None
    geometry_type: str | None = None
    crs: str | None = None
    status: MeasurementStatus
    measurements: dict[str, float] = {}
    projected_crs: str | None = None
    message: str | None = None


class MeasurementSummary(BaseModel):
    total_area_m2: float
    total_length_m: float
    status_counts: dict[str, int]


class MeasurementsResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    summary: MeasurementSummary
    features: list[FeatureMeasurement]
