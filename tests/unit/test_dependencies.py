from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet


def project_metadata():
    return tomllib.loads(
        (Path(__file__).parents[2] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )


def requirements_by_name(values):
    return {
        Requirement(value).name: Requirement(value)
        for value in values
    }


def test_core_uses_released_schema_without_importer_dependency():
    values = project_metadata()["project"]["dependencies"]
    requirements = requirements_by_name(values)

    assert requirements["biomero-schema"].specifier == SpecifierSet(
        ">=0.2,<0.3"
    )
    assert "biomero-importer" not in requirements


def test_full_extra_pins_importer_ezomero_version():
    values = project_metadata()["project"]["optional-dependencies"]["full"]
    requirements = requirements_by_name(values)

    assert str(requirements["ezomero"].specifier) == "==3.2.3"
