import tomllib
from pathlib import Path

from app import __version__


def test_version_matches_pyproject() -> None:
    pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file)

    assert __version__ == project["project"]["version"]
