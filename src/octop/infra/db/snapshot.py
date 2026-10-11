"""Prepare an offline recovery database without starting restored automation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path, PureWindowsPath
from typing import Any


def prepare_history_recovery(archive: Path, original_database: str, database: Path) -> None:
    with closing(sqlite3.connect(archive)) as connection, connection:
        row = connection.execute("SELECT version, identity FROM archive_meta").fetchone()
        if row != (1, original_database):
            raise ValueError("snapshot_history_identity")
        connection.execute("UPDATE archive_meta SET identity=?", (str(database),))


def prepare_recovery(database: Path, source_home: str, destination: Path) -> None:
    def remap(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: remap(item)
                if key in {"root_dir", "workspace_dir", "system_files_path"}
                or isinstance(item, dict)
                else item
                for key, item in value.items()
            }
        if isinstance(value, str) and source_home:
            normalized = value.replace("\\", "/")
            old = source_home.replace("\\", "/").rstrip("/")
            compared = normalized.casefold() if PureWindowsPath(old).drive else normalized
            prefix = old.casefold() if PureWindowsPath(old).drive else old
            if compared == prefix or compared.startswith(prefix + "/"):
                return destination.as_posix() + normalized[len(old) :]
        return value

    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "agents" in tables:
            for agent_id, raw in connection.execute(
                "SELECT agent_id, config_json FROM agents"
            ).fetchall():
                config = json.loads(raw or "{}")
                for key in ("workspace_dir", "system_files_path", "backend"):
                    if key in config:
                        config[key] = remap(config[key])
                connection.execute(
                    "UPDATE agents SET config_json=?, last_state='stopped' WHERE agent_id=?",
                    (json.dumps(config, ensure_ascii=False), agent_id),
                )
        for table in ("channels", "cron_jobs", "proactive_care_config"):
            if table in tables:
                connection.execute(f"UPDATE {table} SET enabled=0")
        if "bridge_connections" in tables:
            connection.execute("UPDATE bridge_connections SET auto_reconnect=0")
