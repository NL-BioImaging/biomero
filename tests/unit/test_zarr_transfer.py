"""Real encoded pixels survive temporary format conversion, without source edits."""
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
zarr = pytest.importorskip("zarr")

from biomero.zarr_transfer import convert_zarr_for_transfer, zarr_ome_version


def attrs(value, version):
    return value if version == 2 else {"ome": {"version": "0.5", **value}}


def fixture(root, version, plate=False):
    group = zarr.open_group(root, mode="w", zarr_format=version)
    if plate:
        group.attrs.update(attrs({"plate": {"name": "Plate", "wells": [{"path": "A/1"}]}}, version))
        well = group.create_group("A/1", attributes=attrs({"well": {"images": [{"path": "0"}]}}, version))
        image = well.create_group("0")
    else:
        image = group
    ngff = {"multiscales": [{"axes": [{"name": "y", "type": "space", "unit": "micrometer"},
                                     {"name": "x", "type": "space", "unit": "micrometer"}],
                              "datasets": [{"path": "s0", "coordinateTransformations": [
                                  {"type": "scale", "scale": [0.7, 0.9]}]}]}],
            "omero": {"channels": [{"label": "DNA"}]}}
    if version == 2:
        ngff["multiscales"][0]["version"] = "0.4"
    image.attrs.update(attrs(ngff, version))
    image.attrs["experiment"] = {"condition": "treated"}
    data = np.arange(165, dtype="uint16").reshape(11, 15)
    kwargs = {"shards": (8, 10), "dimension_names": ("y", "x")} if version == 3 else {}
    image.create_array("s0", data=data, chunks=(4, 5), **kwargs)
    labels = image.create_group("labels", attributes=attrs({"labels": ["cells"]}, version))
    label_attrs = {**ngff, "image-label": {"source": {"image": "../../"}}}
    cells = labels.create_group("cells", attributes=attrs(label_attrs, version))
    cells.create_array("s0", data=data.astype("uint32") + 65537, chunks=(4, 5), **kwargs)
    (root / "measurements.csv").write_text("field,value\n0,7\n")
    (root / ".biomero-canonical.json").write_text('{"source": "original"}')
    return data


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("source_version", [2, 3])
@pytest.mark.parametrize("plate", [False, True])
def test_convert_preserves_pixels_labels_metadata_and_source(tmp_path, source_version, plate):
    root = tmp_path / "source.zarr"
    data = fixture(root, source_version, plate)
    before = snapshot(root)
    target = tmp_path / "transfer.zarr"
    target_version = 5 - source_version

    convert_zarr_for_transfer(root, target, f"0.{target_version + 2}", max_chunk_bytes=40)

    assert zarr_ome_version(target) == f"0.{target_version + 2}"
    group = zarr.open_group(target, mode="r")
    image = group["A/1/0"] if plate else group
    np.testing.assert_array_equal(image["s0"][:], data)
    np.testing.assert_array_equal(image["labels/cells/s0"][:], data.astype("uint32") + 65537)
    assert image["labels/cells/s0"].dtype == np.dtype("uint32")
    assert np.prod(image["s0"].chunks) * image["s0"].dtype.itemsize <= 40
    ngff = image.attrs.asdict()
    assert ngff["experiment"] == {"condition": "treated"}
    if target_version == 3:
        assert image["s0"].metadata.dimension_names == ("y", "x")
        assert image["labels/cells/s0"].metadata.dimension_names == ("y", "x")
        ngff = ngff["ome"]
        assert ngff["version"] == "0.5"
        assert "version" not in ngff["multiscales"][0]
    else:
        assert ngff["multiscales"][0]["version"] == "0.4"
    assert ngff["multiscales"][0]["datasets"][0]["coordinateTransformations"][0]["scale"] == [0.7, 0.9]
    assert ngff["omero"]["channels"][0]["label"] == "DNA"
    assert (target / "measurements.csv").read_bytes() == before["measurements.csv"]
    assert not (target / ".biomero-canonical.json").exists()
    assert snapshot(root) == before


def test_conversion_failure_leaves_no_partial_output_or_source_changes(tmp_path, monkeypatch):
    root, target = tmp_path / "source.zarr", tmp_path / "transfer.zarr"
    fixture(root, 3)
    before = snapshot(root)

    def fail(*args, **kwargs):
        raise RuntimeError("injected read failure")
    monkeypatch.setattr(zarr.Array, "__getitem__", fail)
    with pytest.raises(RuntimeError, match="injected"):
        convert_zarr_for_transfer(root, target, "0.4")
    assert not target.exists()
    assert snapshot(root) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [root.name]


def test_existing_destination_is_never_overwritten(tmp_path):
    root, target = tmp_path / "source.zarr", tmp_path / "target.zarr"
    fixture(root, 2)
    fixture(target, 3)
    before = snapshot(target)
    with pytest.raises(FileExistsError):
        convert_zarr_for_transfer(root, target, "0.5")
    assert snapshot(target) == before


@pytest.mark.parametrize("inside", [True, False])
def test_source_and_destination_must_not_overlap(tmp_path, inside):
    root = tmp_path / "source.zarr"
    fixture(root, 2)
    target = root / "child" if inside else root
    with pytest.raises(ValueError, match="overlap"):
        convert_zarr_for_transfer(root, target, "0.5")


def test_big_endian_v2_pixels_convert_exactly(tmp_path):
    source, target = tmp_path / "source.zarr", tmp_path / "target.zarr"
    fixture(source, 2)
    group = zarr.open_group(source, mode="a")
    del group["s0"]
    pixels = np.array([[0, 1, 256, 32768, 65535]], dtype=">u2")
    group.create_array("s0", data=pixels, chunks=(1, 2))
    before = snapshot(source)
    convert_zarr_for_transfer(source, target, "0.5")
    np.testing.assert_array_equal(zarr.open_group(target, mode="r")["s0"][:], pixels)
    assert snapshot(source) == before


def test_conversion_reads_bounded_slices(tmp_path, monkeypatch):
    root, target = tmp_path / "source.zarr", tmp_path / "target.zarr"
    fixture(root, 3)
    getitem = zarr.Array.__getitem__
    reads = []

    def bounded_read(array, selection):
        assert isinstance(selection, tuple)
        size = np.prod([part.stop - part.start for part in selection]) * array.dtype.itemsize
        assert size <= 40
        reads.append(size)
        return getitem(array, selection)

    monkeypatch.setattr(zarr.Array, "__getitem__", bounded_read)
    convert_zarr_for_transfer(root, target, "0.4", max_chunk_bytes=40)
    assert len(reads) > 2
