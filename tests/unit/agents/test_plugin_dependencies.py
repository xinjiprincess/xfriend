"""Startup skips pip only when dependency versions can be established locally."""

from importlib import metadata
from pathlib import Path

import pytest
from octop_harness.plugins import PluginManifest

from octop.infra.agents.plugins.dependencies import dependencies_satisfied


def test_checks_installed_version_and_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "version", lambda name: "2.0")
    manifest = PluginManifest.from_dict({"id": "test", "version": "1", "requires": ["foo>=1"]})
    assert dependencies_satisfied(tmp_path, manifest)
    (tmp_path / "requirements.txt").write_text("# dependencies\nbar>=3\n")
    assert not dependencies_satisfied(tmp_path, manifest)
    (tmp_path / "requirements.txt").write_text("bar>=1 # comment\n")
    assert dependencies_satisfied(tmp_path, manifest)


@pytest.mark.parametrize(
    "requirement", ["-r other.txt", "foo[extra]", "foo @ https://example.com/foo.whl"]
)
def test_complex_dependencies_keep_pip_path(tmp_path: Path, requirement: str) -> None:
    manifest = PluginManifest.from_dict({"id": "test", "version": "1", "requires": [requirement]})
    assert not dependencies_satisfied(tmp_path, manifest)


def test_missing_package_requires_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing)
    manifest = PluginManifest.from_dict({"id": "test", "version": "1", "requires": ["missing>=1"]})
    assert not dependencies_satisfied(tmp_path, manifest)
