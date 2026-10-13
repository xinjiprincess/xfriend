"""Optional host contribution metadata layered over existing harness plugins."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from pydantic import BaseModel, ConfigDict, Field

from octop import __version__
from octop.infra.agents.plugins.metadata import PluginMetadata


class Contribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["engine", "expert", "connector"]
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    engine: Literal["native", "dsh"] = "native"
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    system_prompt: str = Field(default="", max_length=16000)
    default_model: str | None = None
    runtime: str | None = None


class CapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    version: str
    host: str
    source: str
    dependencies: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    contributions: list[Contribution] = Field(min_length=1)


def load_capabilities(
    directory: Path, plugin_id: str, *, dependencies_root: Path | None = None
) -> CapabilityManifest | None:
    path = directory / "xfriend.json"
    if not path.is_file():
        return None
    manifest = CapabilityManifest.model_validate(json.loads(path.read_text("utf-8")))
    if manifest.id != plugin_id:
        raise ValueError("Capability manifest identity mismatch")
    if not SpecifierSet(manifest.host).contains(__version__):
        raise ValueError("Plugin host version is incompatible")
    if len({item.id for item in manifest.contributions}) != len(manifest.contributions):
        raise ValueError("Duplicate contribution identifier")
    for dependency in manifest.dependencies:
        requirement = Requirement(dependency)
        candidate = (dependencies_root or directory.parent) / requirement.name / "plugin.yaml"
        if not candidate.is_file():
            raise ValueError(f"Missing plugin dependency: {requirement.name}")
        installed = PluginMetadata.load(candidate)
        if not requirement.specifier.contains(str(installed.version)):
            raise ValueError(f"Incompatible plugin dependency: {requirement.name}")
    return manifest
