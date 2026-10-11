"""Avoid invoking pip at every boot when a plugin's dependencies are satisfied."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

from octop_harness.plugins import PluginManifest
from packaging.requirements import InvalidRequirement, Requirement


def dependencies_satisfied(plugin_dir: Path, manifest: PluginManifest) -> bool:
    requirements = list(manifest.requires)
    file = plugin_dir / "requirements.txt"
    if file.is_file():
        requirements.extend(
            line.split(" #", 1)[0].strip()
            for line in file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    for raw in requirements:
        try:
            requirement = Requirement(raw)
        except InvalidRequirement:
            # Let pip interpret nested requirements, options and editable installs.
            return False
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        # Installed version alone cannot establish the provenance of URL requirements.
        if requirement.url or requirement.extras:
            return False
        try:
            version = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            return False
        if not requirement.specifier.contains(version, prereleases=True):
            return False
    return True
