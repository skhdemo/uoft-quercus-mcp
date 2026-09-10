"""Packaging / rebrand smoke checks."""

from __future__ import annotations

import tomllib
from pathlib import Path

from uoft_quercus_mcp.quercus.settings import DEFAULT_DOWNLOAD_DIR, QuercusSettings

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_scripts_include_primary_and_deprecated_alias() -> None:
    data = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text())
    assert data["project"]["name"] == "uoft-quercus-mcp"
    scripts = data["project"]["scripts"]
    assert scripts["uoft-quercus-mcp"] == "uoft_quercus_mcp.__main__:main"
    assert scripts["uoft-timetable-mcp"] == "uoft_quercus_mcp.__main__:main"


def test_pyproject_declares_pypi_listing_metadata() -> None:
    data = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text())
    project = data["project"]
    assert project["readme"] == "README.md"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"]
    urls = project["urls"]
    assert urls["Homepage"] == "https://github.com/skhdemo/uoft-quercus-mcp"
    assert urls["Repository"] == "https://github.com/skhdemo/uoft-quercus-mcp"
    assert urls["Issues"].endswith("/issues")
    assert urls["Changelog"].endswith("/CHANGELOG.md")


def test_default_download_dir_uses_new_cache_path() -> None:
    assert DEFAULT_DOWNLOAD_DIR == "~/.cache/uoft-quercus-mcp/files"
    settings = QuercusSettings()
    assert (
        settings.resolved_download_dir
        == Path("~/.cache/uoft-quercus-mcp/files").expanduser()
    )
