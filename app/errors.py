"""Domain exceptions. The API layer maps these onto HTTP responses."""


class GeoFileError(Exception):
    """Base class for problems with an uploaded geospatial file."""


class UnsupportedFileTypeError(GeoFileError):
    """The file extension / format is not one we accept (-> HTTP 415)."""


class InvalidGeoFileError(GeoFileError):
    """The file is of an accepted type but cannot be processed (-> HTTP 422)."""
