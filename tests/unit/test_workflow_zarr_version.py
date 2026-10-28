import configparser

import pytest

from biomero.slurm_client import SlurmClient


def config(value=None):
    result = configparser.ConfigParser(allow_no_value=True)
    if value is not None:
        result.read_dict({"UI": {"zarr_v3_workflows": value}})
    return result


@pytest.mark.parametrize("value", [None, "", "[]", "false", "null", "seg", '"seg"', '{}', '[1]'])
def test_missing_or_invalid_policy_defaults_to_v2(value):
    assert SlurmClient.resolve_workflow_zarr_version("seg", configs=config(value)) == "0.4"


def test_v3_is_per_workflow_opt_in():
    settings = config('["seg"]')
    assert SlurmClient.resolve_workflow_zarr_version("seg", configs=settings) == "0.5"
    assert SlurmClient.resolve_workflow_zarr_version("other", configs=settings) == "0.4"


@pytest.mark.parametrize("descriptor", [
    {"schema-version": "bilayers:0.1.0"},
    {"schema-version": "boutiques-0.5"},
    {},
])
def test_native_zarr_opt_in_is_independent_of_descriptor_style(descriptor):
    assert SlurmClient.resolve_workflow_zarr_version(
        "seg", configs=config('["seg"]'), descriptor=descriptor, use_zarr=True) == "0.5"


@pytest.mark.parametrize("descriptor", [None, {"schema-version": "boutiques-0.5"},
                                        {"schema-version": "bilayers:0.1.0"}])
def test_tiff_conversion_always_uses_v2(descriptor):
    assert SlurmClient.resolve_workflow_zarr_version(
        "seg", configs=config('["seg"]'), descriptor=descriptor, use_zarr=False) == "0.4"


def test_policy_reads_the_authoritative_config(monkeypatch):
    monkeypatch.setattr(SlurmClient, "load_config", lambda: config('["seg"]'))
    assert SlurmClient.resolve_workflow_zarr_version("seg") == "0.5"
