import configparser
import os

from biomero.constants import slurm_env
from biomero.slurm_client import SlurmClient


def _write_config(path, workflow):
    path.write_text(
        "[WORKFLOWS]\n"
        f"{workflow}={workflow}\n"
        f"{workflow}_repo=https://example.test/{workflow}\n"
        f"{workflow}_job=jobs/{workflow}.sh\n",
        encoding="utf-8",
    )


def test_default_config_paths_remain_layered(monkeypatch):
    monkeypatch.delenv(slurm_env.BIOMERO_SLURM_CONFIG_FILE, raising=False)

    paths = SlurmClient.get_config_paths("extra.ini")

    assert paths == [
        os.path.expanduser(SlurmClient._DEFAULT_CONFIG_PATH_1),
        os.path.expanduser(SlurmClient._DEFAULT_CONFIG_PATH_2),
        os.path.expanduser(SlurmClient._DEFAULT_CONFIG_PATH_3),
        "extra.ini",
    ]


def test_authoritative_config_file_replaces_all_default_layers(
        monkeypatch, tmp_path):
    system_config = tmp_path / "system.ini"
    home_config = tmp_path / "home.ini"
    explicit_config = tmp_path / "explicit.ini"
    authoritative_config = tmp_path / "authoritative.ini"
    _write_config(system_config, "system")
    _write_config(home_config, "home")
    _write_config(explicit_config, "explicit")
    _write_config(authoritative_config, "authoritative")
    monkeypatch.setattr(SlurmClient, "_DEFAULT_CONFIG_PATH_1", str(system_config))
    monkeypatch.setattr(SlurmClient, "_DEFAULT_CONFIG_PATH_2", str(home_config))
    monkeypatch.setattr(SlurmClient, "_DEFAULT_CONFIG_PATH_3", str(home_config))
    monkeypatch.setenv(
        slurm_env.BIOMERO_SLURM_CONFIG_FILE,
        str(authoritative_config),
    )

    configs = SlurmClient.load_config(str(explicit_config))

    assert dict(configs.items("WORKFLOWS")) == {
        "authoritative": "authoritative",
        "authoritative_repo": "https://example.test/authoritative",
        "authoritative_job": "jobs/authoritative.sh",
    }
    assert SlurmClient.get_config_paths(str(explicit_config)) == [
        str(authoritative_config)
    ]
    assert SlurmClient.get_config_write_path() == str(authoritative_config)


def test_missing_authoritative_file_does_not_fall_back(monkeypatch, tmp_path):
    fallback_config = tmp_path / "fallback.ini"
    _write_config(fallback_config, "fallback")
    monkeypatch.setattr(SlurmClient, "_DEFAULT_CONFIG_PATH_1", str(fallback_config))
    monkeypatch.setenv(
        slurm_env.BIOMERO_SLURM_CONFIG_FILE,
        str(tmp_path / "missing.ini"),
    )

    configs = SlurmClient.load_config()

    assert isinstance(configs, configparser.ConfigParser)
    assert configs.sections() == []


def test_environment_sources_report_first_active_override(monkeypatch):
    monkeypatch.setenv(slurm_env.GPU_GRES, "legacy-gres")
    monkeypatch.setenv(slurm_env.BIOMERO_GPU_GRES, "preferred-gres")
    monkeypatch.setenv(slurm_env.BIOMERO_DEFAULT_PARTITION, "cpu")
    monkeypatch.setenv("SQLALCHEMY_URL", "postgresql://managed")

    sources = SlurmClient.get_environment_config_sources()

    assert sources["SLURM"]["gpu_gres"] == {
        "source": "environment",
        "name": slurm_env.BIOMERO_GPU_GRES,
    }
    assert sources["SLURM"]["slurm_default_partition"] == {
        "source": "environment",
        "name": slurm_env.BIOMERO_DEFAULT_PARTITION,
    }
    assert sources["ANALYTICS"]["sqlalchemy_url"] == {
        "source": "environment",
        "name": "SQLALCHEMY_URL",
    }
