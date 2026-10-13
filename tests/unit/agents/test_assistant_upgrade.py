"""Existing user content survives the bundled assistant documentation upgrade."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from octop_harness.backends.workspace import BackendWorkspace
from tests.support.fakes import FakeHarnessAgent

from octop.infra.agents.experts import assistant_upgrade as upgrade

_OLD = {
    "skill": b"---\nname: octop-assistant\n---\n# Factory assistant help\n",
    "soul": b"# Factory persona\n",
    "manifest": b'{"id":"general-assistant","label":{"en":"Main"}}',
}


@pytest.fixture
def recognized_factory_files(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    _, current = upgrade._bundled_files()
    hashes = {kind: {upgrade.content_digest(raw, kind)} for kind, raw in _OLD.items()}
    monkeypatch.setattr(upgrade, "_bundled_files", lambda: (hashes, current))
    return current


@pytest.mark.parametrize("prefix", ["", ".xfriend"])
async def test_upgrade_retires_known_old_skill_in_both_storage_roots(
    tmp_path: Path, prefix: str, recognized_factory_files: dict[str, bytes]
) -> None:
    agent = FakeHarnessAgent(workspace_dir=tmp_path, virtual_mode=False, system_files_path=prefix)
    ws = agent.workspace
    raw = BackendWorkspace(ws.backend, ws.workspace_dir, system_files_path="")
    for root in ("skills", ".xfriend/skills", ".octop/skills"):
        await raw.aupload_bytes(f"{root}/octop-assistant/SKILL.md", _OLD["skill"])
        await raw.awrite_text(f"{root}/octop-assistant/references/custom.md", "my notes")
    await raw.aupload_bytes("SOUL.md", _OLD["soul"].replace(b"\n", b"\r\n"))
    await raw.aupload_bytes(".xfriend/manifest.json", _OLD["manifest"])
    await raw.awrite_text("MEMORY.md", "my memory")
    await raw.aupload_bytes("sessions/untouched.json", b'{"history":["hello"]}')

    changed = await upgrade.upgrade_bundled_assistant(ws, template_name="general-assistant")

    assert changed
    assert (
        await ws.adownload_bytes("skills/xfriend-assistant/SKILL.md")
        == recognized_factory_files["skill"]
    )
    for root in ("skills", ".xfriend/skills", ".octop/skills"):
        assert await raw.adownload_bytes(f"{root}/octop-assistant/SKILL.md") is None
        assert await raw.aread_text(f"{root}/octop-assistant/references/custom.md") == "my notes"
    assert await raw.adownload_bytes("SOUL.md") == recognized_factory_files["soul"]
    assert (
        json.loads(await raw.aread_text(".xfriend/manifest.json"))["label"]["zh"] == "Xfriend 助手"
    )
    assert await raw.aread_text("MEMORY.md") == "my memory"
    assert await raw.adownload_bytes("sessions/untouched.json") == b'{"history":["hello"]}'
    assert await upgrade.upgrade_bundled_assistant(ws, template_name="general-assistant") == []


async def test_customized_skill_soul_and_welcome_are_preserved(
    tmp_path: Path, recognized_factory_files: dict[str, bytes]
) -> None:
    ws = FakeHarnessAgent(workspace_dir=tmp_path, virtual_mode=False).workspace
    files = {
        "skills/octop-assistant/SKILL.md": _OLD["skill"] + b"Use my own workflow.\n",
        "skills/xfriend-assistant/SKILL.md": b"# My assistant\n",
        "SOUL.md": _OLD["soul"] + b"My personal instructions.\n",
        "manifest.json": b'{"id":"general-assistant","welcome_message":{"en":"Welcome back"}}',
    }
    await ws.aupload_many(list(files.items()))
    assert await upgrade.upgrade_bundled_assistant(ws, template_name="general-assistant") == []
    for path, content in files.items():
        assert await ws.adownload_bytes(path) == content


async def test_known_legacy_skill_does_not_overwrite_custom_replacement(
    tmp_path: Path, recognized_factory_files: dict[str, bytes]
) -> None:
    ws = FakeHarnessAgent(workspace_dir=tmp_path, virtual_mode=False).workspace
    await ws.aupload_bytes("skills/octop-assistant/SKILL.md", _OLD["skill"])
    await ws.awrite_text("skills/xfriend-assistant/SKILL.md", "my custom help")
    await upgrade.upgrade_bundled_assistant(ws, template_name="general-assistant")
    assert await ws.aread_text("skills/xfriend-assistant/SKILL.md") == "my custom help"
    assert await ws.adownload_bytes("skills/octop-assistant/SKILL.md") is None


async def test_other_expert_and_intentionally_removed_skill_are_not_reseeded(
    tmp_path: Path, recognized_factory_files: dict[str, bytes]
) -> None:
    ws = FakeHarnessAgent(workspace_dir=tmp_path, virtual_mode=False).workspace
    await ws.aupload_bytes("SOUL.md", _OLD["soul"])
    assert await upgrade.upgrade_bundled_assistant(ws, template_name="other") == []
    assert await ws.adownload_bytes("SOUL.md") == _OLD["soul"]
    await upgrade.upgrade_bundled_assistant(ws, template_name="general-assistant")
    assert await ws.adownload_bytes("skills/xfriend-assistant/SKILL.md") is None


def test_shipped_fingerprints_are_sha256_and_current_docs_are_not_legacy() -> None:
    hashes, current = upgrade._bundled_files()
    for kind, values in hashes.items():
        assert values
        assert all(len(value) == 64 and int(value, 16) >= 0 for value in values)
        assert upgrade.content_digest(current[kind], kind) not in values
