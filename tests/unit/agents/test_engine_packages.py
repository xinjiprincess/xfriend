"""Candidates remain inactive until validation; failures preserve runner settings."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from octop.infra.engines import packages as module


async def test_package_integrity_and_activation(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime").write_bytes(b"runtime")
    (source / "entry").write_bytes(b"entry")
    metadata = {
        "id": "dsh",
        "transport": "acp",
        "version": "1",
        "upstream_commit": "a" * 40,
        "runtime": "runtime",
        "entry": "entry",
        "sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in ["runtime", "entry"]
        },
    }
    (source / "engine.json").write_text(json.dumps(metadata))
    current = {"dsh": {"command": "old", "env": {"API_KEY": "private"}}}
    settings = SimpleNamespace(
        load_runners=lambda user_id: current.copy(),
        save_runners=lambda user_id, value: current.update(value),
    )
    manager = module.EnginePackages(tmp_path, SimpleNamespace(settings=settings, sessions={}))
    identity = await manager.install(str(source))
    assert current["dsh"]["command"] == "old"
    calls = []

    class Probe:
        def __init__(self, home, probe_settings):
            assert "env" not in probe_settings.load_runners(1)["dsh"]

        async def create(self, user_id, cwd):
            return SimpleNamespace(acp_id=None)

        async def _open(self, session):
            calls.append("open")
            session.acp_id = "same-session"

        async def close(self, session):
            calls.append("close")

        async def shutdown(self):
            calls.append("shutdown")

    monkeypatch.setattr(module, "DSHService", Probe)
    await manager.activate(1, identity)
    assert calls == ["open", "close", "open", "shutdown"]
    assert current["dsh"]["env"]["API_KEY"] == "private"
    assert (await manager.list(1))[0]["active"]
    previous = current["dsh"]["command"]
    (manager._directory(identity) / "entry").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        await manager.activate(1, identity)
    assert current["dsh"]["command"] == previous
