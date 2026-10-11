"""Verify desktop upgrade snapshots and restore into a fresh, offline home."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from octop.infra.db.snapshot import prepare_history_recovery, prepare_recovery


def _digest(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"size": path.stat().st_size, "sha256": digest}


def inspect_snapshot(database: Path) -> dict[str, Any]:
    manifest = json.loads(database.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != 1
        or manifest.get("application") != "xfriend"
        or not isinstance(manifest.get("database"), dict)
        or not isinstance(manifest.get("files"), dict)
        or any(not isinstance(name, str) for name in manifest["files"])
    ):
        raise ValueError("snapshot_format")
    if _digest(database) != manifest["database"]:
        raise ValueError("snapshot_checksum")
    root = database.with_suffix(".data").resolve()
    if not root.is_dir() or database.with_suffix(".data").is_symlink():
        raise ValueError("snapshot_path")
    for name, expected in manifest["files"].items():
        relative = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or relative.is_absolute()
            or ".." in relative.parts
            or PureWindowsPath(name).drive
        ):
            raise ValueError("snapshot_path")
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError("snapshot_path")
        if _digest(path) != expected:
            raise ValueError("snapshot_checksum")
    return dict(manifest)


def restore_snapshot(database: Path, destination: Path) -> dict[str, Any]:
    """Never overwrite an existing home or touch external workspaces/databases."""
    manifest = inspect_snapshot(database)
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".xfriend-restore-", dir=destination.parent))
    try:
        root = database.with_suffix(".data")
        for name, expected in manifest["files"].items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, target)
            if _digest(target) != expected:
                raise ValueError("snapshot_checksum")
        # The control DB may originally have lived outside the managed home.
        target_db = stage / "xfriend-restored.db"
        if target_db.exists():
            raise ValueError("snapshot_target_collision")
        shutil.copy2(database, target_db)
        if _digest(target_db) != manifest["database"]:
            raise ValueError("snapshot_checksum")
        prepare_recovery(target_db, str(manifest.get("source_home", "")), destination)
        history = stage / "history_v2.sqlite"
        if history.exists():
            prepare_history_recovery(
                history, str(manifest.get("source_database", "")), destination / target_db.name
            )
        config_path = stage / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        config["database"] = {"driver": "sqlite", "sqlite_path": "xfriend-restored.db"}
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        # A same-filesystem rename publishes only a completely validated tree.
        if destination.exists():
            raise FileExistsError(destination)
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage)
        raise
    return {
        "destination": str(destination),
        "files": len(manifest["files"]),
        "verified": True,
        "automation_paused": True,
    }
