"""Tiny SQLite repository (stdlib only). Swap for Postgres/PostGIS in production."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    file_type     TEXT NOT NULL,
    status        TEXT NOT NULL,
    feature_count INTEGER NOT NULL DEFAULT 0,
    crs           TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    completed_at  TEXT
);
CREATE TABLE IF NOT EXISTS features (
    file_id       TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    idx           INTEGER NOT NULL,
    layer         TEXT,
    geometry_type TEXT,
    geometry      TEXT,
    crs           TEXT,
    properties    TEXT NOT NULL,
    measurement   TEXT NOT NULL,
    PRIMARY KEY (file_id, idx)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ files
    def create_file(self, file_id: str, filename: str, file_type: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO files (id, filename, file_type, status, created_at) VALUES (?,?,?,?,?)",
                (file_id, filename, file_type, "PROCESSING", _now()),
            )

    def complete_file(self, file_id: str, feature_count: int, crs: str | None, rows: list[tuple]) -> None:
        """Persist all features and flip the file to COMPLETED in a single transaction."""
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO features (file_id, idx, layer, geometry_type, geometry, crs, properties, measurement)"
                " VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.execute(
                "UPDATE files SET status='COMPLETED', feature_count=?, crs=?, completed_at=? WHERE id=?",
                (feature_count, crs, _now(), file_id),
            )

    def fail_file(self, file_id: str, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE files SET status='FAILED', error=?, completed_at=? WHERE id=?",
                (error, _now(), file_id),
            )

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
            if row is None:
                return None
            info = dict(row)
            types = conn.execute(
                "SELECT COALESCE(geometry_type,'None') AS t, COUNT(*) AS n FROM features "
                "WHERE file_id=? GROUP BY t",
                (file_id,),
            ).fetchall()
            info["geometry_types"] = {r["t"]: r["n"] for r in types}
            return info

    # --------------------------------------------------------------- features
    def get_features(self, file_id: str, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM features WHERE file_id=? ORDER BY idx LIMIT ? OFFSET ?",
                (file_id, limit, offset),
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "feature_id": r["idx"],
                    "layer": r["layer"],
                    "geometry_type": r["geometry_type"],
                    "geometry": json.loads(r["geometry"]) if r["geometry"] else None,
                    "crs": r["crs"],
                    "properties": json.loads(r["properties"]),
                    "measurement": json.loads(r["measurement"]),
                }
            )
        return out

    def measurement_summary(self, file_id: str) -> dict[str, Any]:
        """Aggregate totals across *all* features (not just the requested page)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT json_extract(measurement,'$.status') AS status,"
                " SUM(json_extract(measurement,'$.measurements.area_m2')) AS area,"
                " SUM(json_extract(measurement,'$.measurements.length_m')) AS length,"
                " COUNT(*) AS n FROM features WHERE file_id=? GROUP BY status",
                (file_id,),
            ).fetchall()
        summary = {"total_area_m2": 0.0, "total_length_m": 0.0, "status_counts": {}}
        for r in rows:
            summary["status_counts"][r["status"]] = r["n"]
            summary["total_area_m2"] += r["area"] or 0.0
            summary["total_length_m"] += r["length"] or 0.0
        summary["total_area_m2"] = round(summary["total_area_m2"], 4)
        summary["total_length_m"] = round(summary["total_length_m"], 4)
        return summary
