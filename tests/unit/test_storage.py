import logging

import pytest

from biomero import constants, storage

VARIABLE = constants.slurm_env.ANALYZED_DATA_FOLDER
DEFAULT = constants.results.DEFAULT_ANALYZED_DATA_FOLDER


def test_unset_returns_default(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    assert storage.read_analyzed_data_folder() == DEFAULT


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_returns_default_without_warning(monkeypatch, caplog, value):
    monkeypatch.setenv(VARIABLE, value)
    with caplog.at_level(logging.WARNING):
        assert storage.read_analyzed_data_folder() == DEFAULT
    assert not caplog.records


@pytest.mark.parametrize("value, expected", [
    (".results", ".results"),
    ("  analyzed  ", "analyzed"),
    ("my.folder", "my.folder"),
])
def test_valid_single_folder_name_is_used(monkeypatch, caplog, value,
                                          expected):
    monkeypatch.setenv(VARIABLE, value)
    with caplog.at_level(logging.WARNING):
        assert storage.read_analyzed_data_folder() == expected
    assert not caplog.records


@pytest.mark.parametrize("value", [
    "a/b", "/abs", "..\\x", "a\\b", ".", "..", "../x", "C:\\data", "C:",
])
def test_invalid_value_warns_and_returns_default(monkeypatch, caplog, value):
    monkeypatch.setenv(VARIABLE, value)
    with caplog.at_level(logging.WARNING, logger="biomero.storage"):
        assert storage.read_analyzed_data_folder() == DEFAULT
    warning = caplog.records[-1]
    assert warning.levelno == logging.WARNING
    assert VARIABLE in warning.getMessage()
    assert repr(value) in warning.getMessage()


def test_environment_is_read_on_every_call(monkeypatch):
    monkeypatch.setenv(VARIABLE, ".first")
    assert storage.read_analyzed_data_folder() == ".first"
    monkeypatch.setenv(VARIABLE, ".second")
    assert storage.read_analyzed_data_folder() == ".second"
    monkeypatch.delenv(VARIABLE)
    assert storage.read_analyzed_data_folder() == DEFAULT
