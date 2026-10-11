"""Offline snapshot restoration preserves login and pauses restored automation."""

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

from octop.infra.backup.desktop_snapshot import restore_snapshot
from octop.infra.history.store import HistoryStore
from tests.support.app import octop_client
from tests.support.auth import auth_header


async def test_restored_home_can_login_and_read_user_settings(env, tmp_path: Path) -> None:
    _client, server, _auth = env
    assert server.services is not None
    server.services.settings_repo.set("assistant_voice:1", '{"tts":"edge"}')
    home = server.paths.root
    database = server.services.config.database.resolve_sqlite_path(home)
    await server.stop()
    history = HistoryStore(home / "history_v2.sqlite", identity=str(database.resolve()))
    history.close()
    source = Path(__file__).parents[2] / "desktop" / "src" / "portable_upgrade.go"
    match = re.search(
        r"const sqliteBackupScript = `(.*?)`", source.read_text(encoding="utf-8"), re.S
    )
    assert match is not None
    backup = tmp_path / "recovery.db"
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", match[1], str(database), str(backup), str(home)],
        check=True,
        timeout=60,
    )
    destination = tmp_path / "recovered-home"
    result = await asyncio.to_thread(restore_snapshot, backup, destination)
    assert result["automation_paused"]
    async with octop_client(destination) as (client, restored):
        auth = await auth_header(client)
        response = await client.get("/api/auth/me", headers=auth)
        assert response.status_code == 200
        assert response.json()["username"] == "admin"
        assert restored.services is not None
        assert restored.app_runtime is not None
        assert restored.app_runtime.history_archive is not None
        assert json.loads(restored.services.settings_repo.get("assistant_voice:1"))["tts"] == "edge"
        assert all(row.last_state == "stopped" for row in restored.services.agent_repo.list_all())
        for row in restored.services.agent_repo.list_all():
            workspace = json.loads(row.config_json or "{}").get("workspace_dir", "")
            if str(home).replace("\\", "/") in workspace.replace("\\", "/"):
                raise AssertionError("Restored agent still points at the original managed home")
