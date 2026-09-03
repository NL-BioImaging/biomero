from pathlib import Path
import tomllib

from packaging.requirements import Requirement


def test_full_extra_includes_script_runtime_dependencies():
    pyproject = tomllib.loads(
        (Path(__file__).parents[2] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    requirements = {
        Requirement(value).name: Requirement(value)
        for value in pyproject["project"]["optional-dependencies"]["full"]
    }

    importer = requirements["biomero-importer"]
    assert importer.extras == {"identity"}
    assert str(importer.specifier) == "<2,>=1.5.0b2"
    assert str(requirements["ezomero"].specifier) == "==3.2.3"
