"""Public engine identity for session compatibility, excluding runner credentials."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def runner_identity(runner: dict[str, Any]) -> dict[str, str]:
    command = str(runner.get("command", ""))
    arguments = list(runner.get("args", []))
    identity: dict[str, Any] = {"command": command, "args": arguments}
    version = "unversioned"
    manifest = Path(command).parent / "engine.json"
    if Path(command).is_absolute() and manifest.is_file():
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        if metadata.get("id") != "dsh":
            raise ValueError("ENGINE_MANIFEST_INVALID")
        version = str(metadata["version"])
        identity["metadata"] = {
            key: metadata.get(key) for key in ("version", "upstream_commit", "sha256")
        }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {"version": version, "signature": digest}
