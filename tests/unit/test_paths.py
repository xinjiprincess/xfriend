"""tests/unit/test_paths.py"""

from __future__ import annotations

from pathlib import Path

from octop.infra.utils.paths import PathLayout


def test_root_paths(tmp_path: Path):
    p = PathLayout(tmp_path / ".xfriend")
    assert p.root == tmp_path / ".xfriend"
    assert p.db == tmp_path / ".xfriend" / "xfriend.db"
    assert p.logs_dir == tmp_path / ".xfriend" / "logs"
    assert p.log == tmp_path / ".xfriend" / "logs" / "xfriend.log"
    assert p.config == tmp_path / ".xfriend" / "config.json"


def test_log_dir_creation(tmp_path: Path):
    p = PathLayout(tmp_path / ".xfriend")
    assert not (tmp_path / ".xfriend" / "logs").exists()
    assert p.ensure_log() == tmp_path / ".xfriend" / "logs" / "xfriend.log"
    assert (tmp_path / ".xfriend" / "logs").is_dir()
    assert p.log == tmp_path / ".xfriend" / "logs" / "xfriend.log"


def test_user_dir_uses_username(tmp_path: Path):
    p = PathLayout(tmp_path / ".xfriend")
    assert p.user_dir("alice") == tmp_path / ".xfriend" / "users" / "alice"


def test_agent_workspace(tmp_path: Path):
    p = PathLayout(tmp_path / ".xfriend")
    ws = p.agent_workspace("agent01")
    assert ws == tmp_path / ".xfriend" / "agents" / "agent01"


def test_ensure_root_creates_dir(tmp_path: Path):
    p = PathLayout(tmp_path / ".xfriend")
    p.ensure_root()
    assert (tmp_path / ".xfriend").is_dir()


def test_ensure_agent_workspace(tmp_path: Path):
    p = PathLayout(tmp_path / ".xfriend")
    # New global path
    out = p.ensure_agent_workspace("a1")
    assert out.is_dir()
    assert out == tmp_path / ".xfriend" / "agents" / "a1"


def test_backups_dir(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".xfriend")
    assert p.backups_dir == tmp_path / ".xfriend" / "backups"
    assert p.ensure_backups_dir().is_dir()


def test_knowledge_dir(tmp_path: Path) -> None:
    p = PathLayout(tmp_path / ".xfriend")
    assert p.knowledge_dir == tmp_path / ".xfriend" / "knowledge"


def test_path_layout_from_env_defaults_to_dot_octop(monkeypatch) -> None:
    monkeypatch.delenv("OCTOP_HOME", raising=False)
    assert PathLayout.from_env().root == Path.home() / ".xfriend"


def test_path_layout_from_env_honors_octop_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    assert PathLayout.from_env().root == tmp_path
