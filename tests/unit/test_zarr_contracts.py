import pytest

from biomero.zarr_contracts import (
    CANONICAL_PLATE_IMAGE_NAMESPACE,
    CANONICAL_PLATE_LABEL_NAMESPACE,
    CANONICAL_SOURCE_NAMESPACE,
    CANONICAL_PLATE_SOURCE_NAMESPACE,
    SHALLOW_COLLECTION_NAMESPACE,
    CanonicalInput,
    CanonicalPlateImageRecord,
    CanonicalPlateIndex,
    CanonicalPlateLabelRecord,
    CanonicalPlateSource,
    CanonicalZarrSource,
    ManagedZarrNode,
    PixelIdentity,
    ShallowZarrReference,
    ZarrLabelComponent,
)


def test_shared_shallow_contract_is_reexported():
    assert SHALLOW_COLLECTION_NAMESPACE == "biomero.zarr.shallow"
    assert ShallowZarrReference.__module__ == "biomero_schema.zarr"
    assert ManagedZarrNode.__module__ == "biomero_schema.zarr"
    assert ZarrLabelComponent.__module__ == "biomero_schema.zarr"
    assert CANONICAL_PLATE_SOURCE_NAMESPACE == "biomero.zarr.plate-source"
    assert CANONICAL_PLATE_IMAGE_NAMESPACE.endswith(".image")
    assert CANONICAL_PLATE_LABEL_NAMESPACE.endswith(".label")
    assert CanonicalPlateSource.__module__ == "biomero_schema.zarr"
    assert CanonicalPlateIndex.__module__ == "biomero_schema.zarr"
    assert CanonicalPlateImageRecord.__module__ == "biomero_schema.zarr"
    assert CanonicalPlateLabelRecord.__module__ == "biomero_schema.zarr"


@pytest.fixture
def pixel_identity():
    return PixelIdentity(
        node_path=".",
        role="image",
        iscc_code="ISCC:KPIXEL",
        data_code="ISCC:GDATA",
        instance_code="ISCC:IINSTANCE",
        tool_version="0.1.0",
        imagewalk_revision="draft-2026-06",
        shape=(1, 2, 3, 64, 64),
        dtype="uint16",
        axes=("t", "c", "z", "y", "x"),
        coordinate_transformations=(
            {"type": "scale", "scale": [1, 1, 2, 0.5, 0.5]},
        ),
    )


@pytest.fixture
def canonical_source(pixel_identity):
    return CanonicalZarrSource(
        storage_root="group-5-data",
        relative_path="project/.processed/Image-3207.g1.ome.zarr",
        node_path=".",
        source_object_type="Image",
        source_object_id=3207,
        source_generation=1,
        interchange_profile="ngff-0.4-zarr-v2",
        pixel_identity=pixel_identity,
        pixel_identity_origin="raw",
        canonical_pixel_verified=True,
        store_identity="ISCC:KSTORE",
    )


def test_pixel_identity_round_trip(pixel_identity):
    restored = PixelIdentity.from_dict(pixel_identity.to_dict())

    assert restored == pixel_identity
    assert restored.shape == (1, 2, 3, 64, 64)
    assert restored.axes == ("t", "c", "z", "y", "x")


def test_canonical_source_round_trips_through_annotation(canonical_source):
    values = canonical_source.to_annotation_values()
    restored = CanonicalZarrSource.from_annotation_values(values)

    assert CANONICAL_SOURCE_NAMESPACE == "biomero.zarr.source"
    assert values["schema"] == "1"
    assert values["canonicalPixelVerified"] == "true"
    assert restored == canonical_source


def test_canonical_source_parses_string_false_as_unverified(canonical_source):
    value = canonical_source.to_dict()
    value["canonicalPixelVerified"] = "false"

    restored = CanonicalZarrSource.from_dict(value)

    assert restored.canonical_pixel_verified is False


@pytest.mark.parametrize(
    "relative_path",
    ["/data/source.ome.zarr", "../source.ome.zarr", "safe/../../escape.zarr"],
)
def test_canonical_source_rejects_unsafe_managed_paths(
    pixel_identity, relative_path
):
    with pytest.raises(ValueError, match="relative managed path"):
        CanonicalZarrSource(
            storage_root="group-5-data",
            relative_path=relative_path,
            node_path=".",
            source_object_type="Image",
            source_object_id=3207,
            source_generation=1,
            interchange_profile="ngff-0.4-zarr-v2",
            pixel_identity=pixel_identity,
            pixel_identity_origin="raw",
            canonical_pixel_verified=True,
        )


def test_canonical_input_round_trip(canonical_source):
    canonical_input = CanonicalInput(
        ordinal=0,
        selected_object_type="Image",
        selected_object_id=3207,
        source=canonical_source,
    )

    assert CanonicalInput.from_dict(canonical_input.to_dict()) == canonical_input


def test_label_identity_is_independent_node():
    label = PixelIdentity(
        node_path="labels/cells",
        role="label",
        iscc_code="ISCC:KLABEL",
        data_code="ISCC:GLABEL",
        instance_code="ISCC:ILABEL",
        tool_version="0.1.0",
        imagewalk_revision="draft-2026-06",
        shape=(1, 1, 3, 64, 64),
        dtype="uint16",
        axes=("t", "c", "z", "y", "x"),
    )

    assert label.role == "label"
    assert label.node_path == "labels/cells"
