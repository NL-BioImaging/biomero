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
    assert str(requirements["numpy"].specifier) == "<3,>=2"
    assert str(requirements["tifffile"].specifier) == "==2026.3.3"
    assert str(requirements["scikit-image"].specifier) == "==0.25.2"
    assert str(requirements["omero-rois"].specifier) == "==0.4.1"
    assert str(requirements["dask"].specifier) == "==2026.1.1"
    assert str(requirements["distributed"].specifier) == "==2026.1.1"
