"""Temporary NGFF format conversion; canonical stores are always read-only.

Zarr is an optional dependency supplied by ``biomero[full]``. Conversion reads
bounded slices, including labels and pyramid levels, and publishes the new
store only after every array has been copied. Source codecs may decode a larger
source chunk or shard internally. No identities or canonical copies are made.
"""
from copy import deepcopy
from itertools import product
import json
from math import prod
from pathlib import Path
import shutil
from uuid import uuid4


_NGFF_KEYS = {"multiscales", "omero", "plate", "well", "labels", "image-label"}
_ZARR_METADATA = {".zattrs", ".zgroup", ".zarray", ".zmetadata", "zarr.json"}


def zarr_ome_version(root):
    """Read the root format without opening arrays or hashing pixels."""
    root = Path(root)
    if (root / "zarr.json").is_file():
        if any((root / name).exists() for name in (".zgroup", ".zattrs", ".zarray")):
            raise ValueError("Ambiguous Zarr v2/v3 root metadata")
        doc = json.loads((root / "zarr.json").read_text(encoding="utf-8"))
        if (doc.get("zarr_format") == 3 and doc.get("node_type") == "group"
                and doc.get("attributes", {}).get("ome", {}).get("version") == "0.5"):
            return "0.5"
    elif (root / ".zgroup").is_file():
        doc = json.loads((root / ".zgroup").read_text(encoding="utf-8"))
        if doc.get("zarr_format") == 2:
            return "0.4"
    raise ValueError(f"Expected an OME-Zarr 0.4/v2 or 0.5/v3 group: {root}")


def _group_attrs(attributes, source_version, target_version):
    attributes = deepcopy(attributes)
    if source_version == 3:
        ome = attributes.pop("ome", {})
        if ome and ome.pop("version", None) != "0.5":
            raise ValueError("Unsupported OME-Zarr metadata version")
        if set(ome).intersection(attributes):
            raise ValueError("Conflicting NGFF and extension attribute names")
        attributes.update(ome)
    elif "ome" in attributes:
        raise ValueError("Cannot convert an ambiguous v2 'ome' namespace")

    versioned = list(attributes.get("multiscales", []))
    versioned.extend(attributes[key] for key in ("plate", "well", "image-label")
                     if key in attributes)
    for value in versioned:
        if not isinstance(value, dict):
            raise ValueError("Invalid NGFF group metadata")
        if value.get("version") not in (None, "0.4", "0.5"):
            raise ValueError("Unsupported NGFF metadata version")
        if target_version == 2:
            value["version"] = "0.4"
        else:
            value.pop("version", None)
    if target_version == 3:
        ome = {key: attributes.pop(key) for key in _NGFF_KEYS if key in attributes}
        if ome:
            attributes["ome"] = {"version": "0.5", **ome}
    return attributes


def _chunk_shape(shape, chunks, itemsize, limit):
    chunks = [max(1, min(chunk, size)) for chunk, size in zip(chunks, shape)]
    while prod(chunks) * itemsize > limit:
        axis = max(range(len(chunks)), key=chunks.__getitem__)
        if chunks[axis] == 1:
            raise ValueError("Chunk limit is smaller than one pixel")
        chunks[axis] = (chunks[axis] + 1) // 2
    return tuple(chunks)


def convert_zarr_for_transfer(source, destination, ome_version, *,
                              max_chunk_bytes=64 * 1024 * 1024):
    """Write a standalone transfer store in the requested format.

    Preserve exact values, labels, calibration and custom group attributes.
    Codec/shard layout may change. BIOMERO storage sidecars are excluded since
    they describe the original store, not this temporary representation.
    """
    import zarr

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Source and destination paths overlap")
    if destination.exists():
        raise FileExistsError(destination)
    if ome_version not in ("0.4", "0.5"):
        raise ValueError(f"Unsupported transfer OME-Zarr version: {ome_version}")
    if not isinstance(max_chunk_bytes, int) or max_chunk_bytes < 1:
        raise ValueError("Chunk limit must be a positive byte count")
    source_version = {"0.4": 2, "0.5": 3}[zarr_ome_version(source)]
    target_version = {"0.4": 2, "0.5": 3}[ome_version]
    original = zarr.open_group(source, mode="r", use_consolidated=False)
    staging = destination.with_name(f".{destination.name}.convert-{uuid4().hex}")

    def copy_group(src, dst, src_path, dst_path, axes=None):
        if src.metadata.zarr_format != source_version:
            raise ValueError("Mixed Zarr formats in source groups")
        dst.attrs.update(_group_attrs(src.attrs.asdict(), source_version, target_version))
        ngff = src.attrs.asdict()
        if source_version == 3:
            ngff = ngff.get("ome", {})
        for multiscale in ngff.get("multiscales", []):
            names = tuple(axis["name"] if isinstance(axis, dict) else axis
                          for axis in multiscale.get("axes", []))
            if axes is not None and names != axes and len(ngff["multiscales"]) > 1:
                raise ValueError("Multiple axis layouts in one NGFF group are unsupported")
            axes = names
        members = set()
        for name, node in src.members():
            members.add(name)
            if isinstance(node, zarr.Group):
                copy_group(node, dst.create_group(name), src_path / name, dst_path / name, axes)
                continue
            if node.metadata.zarr_format != source_version:
                raise ValueError("Mixed Zarr formats in source arrays")
            if node.dtype.kind not in "biuf":
                raise ValueError(f"Unsupported transfer array dtype: {node.dtype}")
            chunks = _chunk_shape(node.shape, node.chunks, node.dtype.itemsize, max_chunk_bytes)
            array_attrs = node.attrs.asdict()
            dimensions = getattr(node.metadata, "dimension_names", None)
            kwargs = {}
            if target_version == 3:
                dimensions = dimensions or array_attrs.get("_ARRAY_DIMENSIONS") or axes
                if dimensions:
                    kwargs["dimension_names"] = tuple(dimensions)
            elif dimensions:
                array_attrs["_ARRAY_DIMENSIONS"] = list(dimensions)
            target = dst.create_array(name, shape=node.shape, chunks=chunks,
                                      dtype=node.dtype, fill_value=node.fill_value,
                                      attributes=array_attrs, **kwargs)
            for starts in product(*(range(0, size, chunk) for size, chunk in zip(node.shape, chunks))):
                selection = tuple(slice(start, min(start + chunk, size))
                                  for start, chunk, size in zip(starts, chunks, node.shape))
                target[selection] = node[selection]
        # Preserve ordinary sidecar files, but never stale storage contracts.
        for path in src_path.iterdir():
            if (path.name in members or path.name in _ZARR_METADATA
                    or path.name.startswith(".biomero-")):
                continue
            if path.is_dir():
                raise ValueError(f"Unrecognized directory in OME-Zarr: {path}")
            shutil.copyfile(path, dst_path / path.name)

    try:
        target = zarr.open_group(staging, mode="w", zarr_format=target_version)
        copy_group(original, target, source, staging)
        target.store.close()
        if destination.exists():
            raise FileExistsError(destination)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination
