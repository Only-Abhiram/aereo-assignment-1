"""File-processing pipeline: load -> extract features -> measure -> persist."""
from __future__ import annotations

import json
import logging
import tempfile
import uuid
from pathlib import Path

from ..config import Settings
from ..db import Database
from ..errors import GeoFileError
from .crs import crs_label
from .loader import detect_file_type, geometry_to_geojson, iter_features, load_layers
from .measurements import measure_geometry

log = logging.getLogger(__name__)


def process_upload(db: Database, settings: Settings, saved_path: Path, filename: str,
                   default_crs: str | None = None) -> str:
    """Process an already-saved upload. Returns the new file id.

    Raises UnsupportedFileTypeError *before* a record is created. Any later failure marks
    the record FAILED (so the client can still GET it) and re-raises.
    """
    file_type = detect_file_type(filename)
    file_id = uuid.uuid4().hex
    db.create_file(file_id, filename, file_type)

    try:
        with tempfile.TemporaryDirectory(prefix="geo_") as tmp:
            layers = load_layers(saved_path, file_type, Path(tmp), settings.max_unzipped_bytes, default_crs)

            rows = []
            for idx, layer, geom, props in iter_features(layers):
                result = measure_geometry(geom, layer.crs)
                rows.append(
                    (
                        file_id,
                        idx,
                        layer.name,
                        geom.geom_type if geom is not None else None,
                        json.dumps(geometry_to_geojson(geom)) if geom is not None else None,
                        crs_label(layer.crs),
                        json.dumps(props),
                        json.dumps(result.to_dict()),
                    )
                )

        crs_values = {r[5] for r in rows}
        file_crs = crs_values.pop() if len(crs_values) == 1 else "MIXED"
        db.complete_file(file_id, len(rows), file_crs, rows)
    except GeoFileError as exc:
        db.fail_file(file_id, str(exc))
        raise _Failed(file_id, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected error while processing %s", filename)
        db.fail_file(file_id, "Unexpected error while processing the file.")
        raise _Failed(file_id, "Unexpected error while processing the file.") from exc
    return file_id


class _Failed(Exception):
    """Processing failed; the FAILED record exists under ``file_id``."""

    def __init__(self, file_id: str, message: str):
        super().__init__(message)
        self.file_id = file_id
        self.message = message


ProcessingFailed = _Failed
