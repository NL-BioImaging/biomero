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


@pytest.mark.parametrize("descriptor, expected", [
    ({"schema-version": "bilayers:0.1.0"}, "0.5"),
    ({"schema-version": "0.5"}, "0.4"),
    ({}, "0.4"),
])
def test_worker_descriptor_enforces_bilayers_only(descriptor, expected):
    assert SlurmClient.resolve_workflow_zarr_version(
        "seg", configs=config('["seg"]'), descriptor=descriptor) == expected


def test_tiff_conversion_always_uses_v2():
    assert SlurmClient.resolve_workflow_zarr_version(
        "seg", configs=config('["seg"]'), use_zarr=False) == "0.4"


def test_policy_reads_the_authoritative_config(monkeypatch):
    monkeypatch.setattr(SlurmClient, "load_config", lambda: config('["seg"]'))
    assert SlurmClient.resolve_workflow_zarr_version("seg") == "0.5"
