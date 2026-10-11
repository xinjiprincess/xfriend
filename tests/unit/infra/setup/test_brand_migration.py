from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.setup.brand_migration import migrate_agent_paths, migrate_local_files
from octop.infra.users.password import hash_password, verify_password
from octop.infra.utils.paths import PathLayout


def test_run_migrates_home_before_saving_port(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from octop.cli.commands import run as run_command

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("OCTOP_HOME", raising=False)
    monkeypatch.delenv("XFRIEND_HOME", raising=False)
    legacy = tmp_path / ".singuxmate"
    legacy.mkdir()
    (legacy / "config.json").write_text('{"port": 18088, "keep": "original"}')
    monkeypatch.setattr(run_command, "_run_uvicorn", lambda **kwargs: None)
    result = CliRunner().invoke(run_command.run, ["--port", "18089"])
    assert result.exit_code == 0, result.output
    assert not legacy.exists()
    config = json.loads((tmp_path / ".xfriend" / "config.json").read_text())
    assert config == {"port": 18089, "keep": "original"}


def test_legacy_accounts_and_workspace_survive_rename(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    legacy = tmp_path / ".singuxmate"
    old_workspace = legacy / "agents" / "main"
    private = old_workspace / ".octop"
    private.mkdir(parents=True)
    (private / "memory.sqlite").write_bytes(b"existing memory")
    (old_workspace / "SOUL.md").write_text("my octop notes", encoding="utf-8")
    (legacy / "config.json").write_text(
        json.dumps({"database": {"driver": "sqlite", "sqlite_path": "octop.db"}})
    )
    db = SqlitePool(legacy / "octop.db")
    run_migrations(db)
    services = build_shared_services(db=db, paths=PathLayout(legacy), config=OctopConfig())
    password_hash = hash_password("MigrationTest!123")
    services.user_repo.create(username="admin", password_hash=password_hash, role="admin")
    services.agent_repo.create(
        agent_id="main",
        user_id=None,
        name="My assistant",
        config_json=json.dumps(
            {
                "workspace_dir": str(old_workspace),
                "system_files_path": ".octop",
                "backend": {"type": "local_shell", "root_dir": str(legacy)},
                "system_prompt": "keep .octop in my notes",
            }
        ),
    )
    with db.transaction() as connection:
        connection.execute(
            "CREATE TABLE migration_account_probe (username TEXT, password_hash TEXT)"
        )
        connection.execute("INSERT INTO migration_account_probe VALUES ('admin', 'unchanged hash')")
    db.close()
    paths = PathLayout(tmp_path / ".xfriend")
    migrate_local_files(paths)
    assert not legacy.exists()
    assert not (paths.root / "octop.db").exists()
    assert json.loads(paths.config.read_text())["database"]["sqlite_path"] == "xfriend.db"
    db = SqlitePool(paths.db)
    try:
        services = build_shared_services(db=db, paths=paths, config=OctopConfig())
        migrate_agent_paths(services, paths)
        migrate_agent_paths(services, paths)
        with db.connect() as connection:
            assert tuple(
                connection.execute("SELECT * FROM migration_account_probe").fetchone()
            ) == ("admin", "unchanged hash")
        user = services.user_repo.get_by_username("admin")
        assert user is not None and user.password_hash == password_hash
        assert verify_password("MigrationTest!123", user.password_hash)
        config = json.loads(services.agent_repo.list_all()[0].config_json)
        assert config["workspace_dir"] == (paths.agents_dir / "main").as_posix()
        assert config["system_files_path"] == ".xfriend"
        assert config["system_prompt"] == "keep .octop in my notes"
        assert (
            paths.agents_dir / "main" / ".xfriend" / "memory.sqlite"
        ).read_bytes() == b"existing memory"
        assert (paths.agents_dir / "main" / "SOUL.md").read_text() == "my octop notes"
    finally:
        db.close()
    migrate_local_files(paths)
    with sqlite3.connect(paths.backups_dir / "xfriend-before-brand-migration.db") as recovery:
        assert (
            recovery.execute("SELECT username FROM migration_account_probe").fetchone()[0]
            == "admin"
        )


def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    paths = PathLayout(tmp_path)
    with sqlite3.connect(tmp_path / "octop.db") as db:
        db.execute("CREATE TABLE old_data (id INTEGER)")
    paths.db.write_bytes(b"destination")
    with pytest.raises(FileExistsError):
        migrate_local_files(paths)
    assert paths.db.read_bytes() == b"destination"
    assert (tmp_path / "octop.db").exists()
