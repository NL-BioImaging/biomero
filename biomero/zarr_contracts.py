"""Compatibility imports for shared BIOMERO Zarr contracts.

The wire schemas live in :mod:`biomero_schema.zarr`. Runtime behavior such as
workflow events remains in this package.
"""

from biomero_schema.zarr import (
    CANONICAL_SOURCE_NAMESPACE,
    CANONICAL_SOURCE_SCHEMA,
    PIXEL_IDENTITY_METHOD,
    CanonicalInput,
    CanonicalInputManifest,
    CanonicalZarrSource,
    PixelIdentity,
)


__all__ = [
    "CANONICAL_SOURCE_NAMESPACE",
    "CANONICAL_SOURCE_SCHEMA",
    "PIXEL_IDENTITY_METHOD",
    "CanonicalInput",
    "CanonicalInputManifest",
    "CanonicalZarrSource",
    "PixelIdentity",
]
