from pathlib import Path
import tomllib

from packaging.requirements import Requirement


def test_full_extra_accepts_importer_ezomero_version():
    pyproject = tomllib.loads(
        (Path(__file__).parents[2] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    requirements = {
        Requirement(value).name: Requirement(value)
        for value in pyproject["project"]["optional-dependencies"]["full"]
    }

    assert "3.2.3" in requirements["ezomero"].specifier
