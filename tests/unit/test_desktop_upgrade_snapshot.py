"""Exercise the actual portable-upgrade backup script with WAL databases."""

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from octop.infra.backup.desktop_snapshot import inspect_snapshot, restore_snapshot


@pytest.mark.parametrize("name", ["../outside", "C:/outside", "/outside", "a\\outside"])
def test_snapshot_rejects_escaping_paths(tmp_path: Path, name: str) -> None:
    import hashlib

    database = tmp_path / "backup.db"
    database.write_bytes(b"database")
    database.with_suffix(".data").mkdir()
    manifest = {
        "format": 1,
        "application": "xfriend",
        "database": {"size": 8, "sha256": hashlib.sha256(b"database").hexdigest()},
        "files": {name: {}},
    }
    database.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot_path"):
        restore_snapshot(database, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_snapshot_preserves_nested_database_and_excludes_runtime(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "desktop" / "src" / "portable_upgrade.go"
    match = re.search(
        r"const sqliteBackupScript = `(.*?)`", source.read_text(encoding="utf-8"), re.S
    )
    assert match is not None
    home = tmp_path / "home"
    home.mkdir()
    database = home / "xfriend.db"
    sessions = home / "engine-data" / "dsh" / "sessions.db"
    sessions.parent.mkdir(parents=True)
    readers = []
    try:
        for path in (database, sessions):
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE messages(text TEXT)")
            conn.execute("INSERT INTO messages VALUES ('保留我的对话')")
            conn.commit()
            readers.append(conn)
        (home / "config.json").write_text('{"setting":"保留配置"}', encoding="utf-8")
        (home / "portable.new").mkdir()
        (home / "portable.new" / "runtime.bin").write_bytes(b"runtime")
        backups = home / "backups"
        backups.mkdir()
        destination = backups / "before-1.2.db"
        subprocess.run(
            [sys.executable, "-c", match[1], str(database), str(destination), str(home)],
            check=True,
            timeout=30,
        )
        snapshot = destination.with_suffix(".data")
        assert not (snapshot / "backups").exists()
        assert not (snapshot / "portable.new").exists()
        assert "保留配置" in (snapshot / "config.json").read_text(encoding="utf-8")
        for path in (destination, snapshot / sessions.relative_to(home)):
            with sqlite3.connect(path) as conn:
                assert conn.execute("SELECT text FROM messages").fetchone()[0] == "保留我的对话"
        manifest = inspect_snapshot(destination)
        assert manifest["scope"] == "managed_home_and_control_database"
        restored = tmp_path / "restored"
        restore_snapshot(destination, restored)
        with sqlite3.connect(restored / "xfriend-restored.db") as conn:
            assert conn.execute("SELECT text FROM messages").fetchone()[0] == "保留我的对话"
        with sqlite3.connect(restored / sessions.relative_to(home)) as conn:
            assert conn.execute("SELECT text FROM messages").fetchone()[0] == "保留我的对话"
        with pytest.raises(FileExistsError):
            restore_snapshot(destination, restored)
        (snapshot / "config.json").write_text("corrupted", encoding="utf-8")
        with pytest.raises(ValueError, match="snapshot_checksum"):
            restore_snapshot(destination, tmp_path / "must-not-exist")
        assert not (tmp_path / "must-not-exist").exists()
    finally:
        for conn in readers:
            conn.close()
