"""Runtime configuration, read from environment variables with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int
    max_unzipped_bytes: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "geo_api.db"


def get_settings() -> Settings:
    mb = 1024 * 1024
    return Settings(
        data_dir=Path(os.getenv("GEO_DATA_DIR", "data")),
        max_upload_bytes=int(os.getenv("GEO_MAX_UPLOAD_MB", "50")) * mb,
        # Guards against zip bombs: limit on the *uncompressed* size of an archive.
        max_unzipped_bytes=int(os.getenv("GEO_MAX_UNZIPPED_MB", "500")) * mb,
    )
