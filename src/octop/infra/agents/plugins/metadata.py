"""Read plugin display metadata without importing or executing the plugin SDK.

The SDK remains authoritative for execution. This projection deliberately reads
only the declarative manifest and directory entries, so listing installed plugins
does not load their entry points or provision dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PluginMetadata:
    id: str
    version: str
    name: str
    kind: str
    description: str
    requires: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> PluginMetadata:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Invalid plugin manifest: {path}")
        plugin_id = str(data.get("id") or "").strip()
        version = str(data.get("version") or "").strip()
        if not plugin_id or not version:
            raise ValueError("plugin.yaml requires id and version")
        kind = str(data.get("kind") or "tool").strip().lower()
        if kind not in ("tool", "skill", "hook"):
            raise ValueError(f"Unsupported plugin kind: {kind!r}")
        requires = data.get("requires") or []
        if not isinstance(requires, list):
            raise ValueError("requires must be a list of pip requirement strings")
        return cls(
            id=plugin_id,
            version=version,
            name=str(data.get("name") or plugin_id).strip(),
            kind=kind,
            description=str(data.get("description") or "").strip(),
            requires=tuple(str(item).strip() for item in requires if str(item).strip()),
        )


def discover_plugin_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [item for item in sorted(root.iterdir()) if (item / "plugin.yaml").is_file()]
