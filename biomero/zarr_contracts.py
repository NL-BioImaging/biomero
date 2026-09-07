"""Compatibility imports for shared BIOMERO Zarr contracts.

The wire schemas live in :mod:`biomero_schema.zarr`. Runtime behavior such as
workflow events remains in this package.
"""

from biomero_schema.zarr import (
    CANONICAL_PLATE_IMAGE_NAMESPACE,
    CANONICAL_PLATE_LABEL_NAMESPACE,
    CANONICAL_SOURCE_NAMESPACE,
    CANONICAL_SOURCE_SCHEMA,
    CANONICAL_PLATE_SOURCE_NAMESPACE,
    PIXEL_IDENTITY_METHOD,
    SHALLOW_COLLECTION_MANIFEST,
    SHALLOW_COLLECTION_NAMESPACE,
    TRANSFER_INPUT_MARKER,
    CanonicalInput,
    CanonicalInputManifest,
    CanonicalPlateImage,
    CanonicalPlateImageRecord,
    CanonicalPlateIndex,
    CanonicalPlateLabelRecord,
    CanonicalPlateSource,
    CanonicalZarrSource,
    ManagedZarrNode,
    PixelIdentity,
    ShallowCollection,
    ShallowImageReference,
    ShallowPlateReference,
    ShallowZarrReference,
    ZarrLabelComponent,
)


__all__ = [
    "CANONICAL_PLATE_IMAGE_NAMESPACE",
    "CANONICAL_PLATE_LABEL_NAMESPACE",
    "CANONICAL_SOURCE_NAMESPACE",
    "CANONICAL_SOURCE_SCHEMA",
    "CANONICAL_PLATE_SOURCE_NAMESPACE",
    "PIXEL_IDENTITY_METHOD",
    "SHALLOW_COLLECTION_MANIFEST",
    "SHALLOW_COLLECTION_NAMESPACE",
    "TRANSFER_INPUT_MARKER",
    "CanonicalInput",
    "CanonicalInputManifest",
    "CanonicalPlateImage",
    "CanonicalPlateImageRecord",
    "CanonicalPlateIndex",
    "CanonicalPlateLabelRecord",
    "CanonicalPlateSource",
    "CanonicalZarrSource",
    "ManagedZarrNode",
    "PixelIdentity",
    "ShallowCollection",
    "ShallowImageReference",
    "ShallowPlateReference",
    "ShallowZarrReference",
    "ZarrLabelComponent",
]
