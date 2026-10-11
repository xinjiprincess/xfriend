"""Preserve local accounts and workspaces when changing the product data layout."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octop.i18n import tr
from octop.infra.agents.workspace.dir import local_backend_root_dir, workspace_dir_from_config
from octop.infra.utils.locale import DEFAULT_LOCALE
from octop.infra.utils.paths import PathLayout

if TYPE_CHECKING:
    from octop.infra.db.services import SharedServices


def _remap(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _remap(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, root) for item in value]
    if not isinstance(value, str):
        return value
    normalized = value.replace("\\", "/")
    for name in (".singuxmate", ".octop"):
        old = (root.parent / name).as_posix()
        if normalized == old or normalized.startswith(old + "/"):
            return root.as_posix() + normalized[len(old) :]
    return value


def _rename(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if destination.exists():
        raise FileExistsError(tr("migration.target_exists", DEFAULT_LOCALE, path=destination))
    source.rename(destination)


def migrate_home(paths: PathLayout) -> None:
    """Run before CLI bind overrides or the desktop WebView create the new root."""
    root = paths.root
    if not root.exists() and root == Path.home() / ".xfriend":
        for name in (".singuxmate", ".octop"):
            source = root.parent / name
            if source.exists():
                _rename(source, root)
                break


def migrate_local_files(paths: PathLayout) -> None:
    """Run before opening the database or configuring file logging."""
    migrate_home(paths)
    root = paths.root
    root.mkdir(parents=True, exist_ok=True)
    config_path = paths.config
    config: dict[str, Any] = {}
    if config_path.exists():
        config = _remap(json.loads(config_path.read_text(encoding="utf-8")), root)
    database = config.get("database", {})
    old_db = root / "octop.db"
    configured = database.get("sqlite_path", "octop.db")
    if (
        database.get("driver", "sqlite") == "sqlite"
        and configured in ("octop.db", str(old_db), old_db.as_posix())
        and old_db.exists()
    ):
        if paths.db.exists():
            raise FileExistsError(tr("migration.target_exists", DEFAULT_LOCALE, path=paths.db))
        # A consistent recovery copy also includes committed WAL pages.
        paths.backups_dir.mkdir(exist_ok=True)
        recovery = paths.backups_dir / "xfriend-before-brand-migration.db"
        with closing(sqlite3.connect(old_db, timeout=5)) as source_db:
            with closing(sqlite3.connect(recovery)) as recovery_db:
                source_db.backup(recovery_db)
            result = source_db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if result and result[0]:
                raise RuntimeError(tr("migration.database_busy", DEFAULT_LOCALE))
        _rename(old_db, paths.db)
        for suffix in ("-wal", "-shm"):
            _rename(Path(str(old_db) + suffix), Path(str(paths.db) + suffix))
        database["sqlite_path"] = "xfriend.db"
        config["database"] = database
    # Covers a restart between the DB rename and writing config.json.
    if (
        paths.db.exists()
        and not old_db.exists()
        and configured in ("octop.db", str(old_db), old_db.as_posix())
    ):
        database["sqlite_path"] = "xfriend.db"
        config["database"] = database
    if config_path.exists():
        temporary = config_path.with_suffix(".migration.tmp")
        temporary.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, config_path)
    if paths.logs_dir.exists():
        for source in paths.logs_dir.glob("octop.log*"):
            destination = source.with_name(source.name.replace("octop.log", "xfriend.log", 1))
            _rename(source, destination)


def migrate_agent_paths(services: SharedServices, paths: PathLayout) -> None:
    """Move private system directories; never rewrite user workspace content."""
    for row in services.agent_repo.list_all(include_disabled=True):
        config = json.loads(row.config_json or "{}")
        updated = _remap(config, paths.root)
        # Only path-bearing fields are remapped, not prompts, names or user text.
        for key in set(config) - {"workspace_dir", "system_files_path", "backend"}:
            updated[key] = config[key]
        workspace = workspace_dir_from_config(
            updated, paths=paths, agent_id=row.agent_id, ensure=False
        )
        backend_root = local_backend_root_dir(updated)
        if backend_root and "/.octop/" in str(updated.get("workspace_dir", "")).replace("\\", "/"):
            _rename(Path(backend_root) / ".octop", Path(backend_root) / ".xfriend")
            updated["workspace_dir"] = updated["workspace_dir"].replace("/.octop/", "/.xfriend/")
            workspace = workspace_dir_from_config(
                updated, paths=paths, agent_id=row.agent_id, ensure=False
            )
        for old, new in (
            (".octop", ".xfriend"),
            (".octop-auth", ".xfriend-auth"),
            (".octop-browser", ".xfriend-browser"),
        ):
            _rename(workspace / old, workspace / new)
        if updated.get("system_files_path") == ".octop":
            updated["system_files_path"] = ".xfriend"
        if updated != config:
            services.agent_repo.update_config(
                row.agent_id, config_json=json.dumps(updated, ensure_ascii=False)
            )
