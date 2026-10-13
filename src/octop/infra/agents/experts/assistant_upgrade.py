"""Upgrade untouched bundled assistant files without rewriting user workspaces."""

from __future__ import annotations

import asyncio
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from octop_harness.backends.workspace import BackendWorkspace

ASSISTANT_TEMPLATE_ID = "general-assistant"
ASSISTANT_NAMES = {"zh": "Xfriend 助手", "en": "Xfriend Assistant"}
LEGACY_ASSISTANT_NAMES = {
    "小通 · 通用助手": "zh",
    "Octop 助手": "zh",
    "Singu Xfriend 助手": "zh",
    "Main · General Assistant": "en",
    "Octop Assistant": "en",
    "Singu Xfriend Assistant": "en",
}
_SKILL_PATH = "skills/xfriend-assistant/SKILL.md"
_LEGACY_SKILL_PATH = "skills/octop-assistant/SKILL.md"
_ROOT = Path(__file__).parent


def content_digest(raw: bytes, kind: str) -> str:
    """Ignore transport newlines/BOM; manifest formatting is not customization."""
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").strip()
    if kind == "manifest":
        text = json.dumps(
            json.loads(text), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _bundled_files() -> tuple[dict[str, set[str]], dict[str, bytes]]:
    # Fingerprints cover previously shipped, unmodified templates. Old product
    # documentation itself is deliberately not bundled as migration payload.
    hashes = json.loads((_ROOT / "assistant_legacy_hashes.json").read_text(encoding="utf-8"))
    source = _ROOT / "library" / ASSISTANT_TEMPLATE_ID
    return (
        {key: set(values) for key, values in hashes.items()},
        {
            "skill": (source / _SKILL_PATH).read_bytes(),
            "soul": (source / "SOUL.md").read_bytes(),
            "manifest": (source / "manifest.json").read_bytes(),
        },
    )


def _is_legacy(raw: bytes | None, kind: str, hashes: dict[str, set[str]]) -> bool:
    if raw is None:
        return False
    try:
        return content_digest(raw, kind) in hashes[kind]
    except (UnicodeError, ValueError):
        return False


async def upgrade_bundled_assistant(
    workspace: BackendWorkspace, *, template_name: str | None
) -> list[str]:
    """Replace known factory content only; leave custom names/files/history alone.

    A prefix-free workspace view addresses the two historical skill roots
    explicitly. All content I/O still goes through BackendWorkspace, including
    remote backends; no host paths or backend-type checks are involved.
    """
    if template_name != ASSISTANT_TEMPLATE_ID:
        return []
    hashes, bundled = await asyncio.to_thread(_bundled_files)
    stored = BackendWorkspace(workspace.backend, workspace.workspace_dir, system_files_path="")
    prefixes = tuple(dict.fromkeys(("", workspace.system_files_path, ".xfriend", ".octop")))
    changed: list[str] = []
    canonical_skill = workspace.system_rel(_SKILL_PATH)
    current_skill = await stored.adownload_bytes(canonical_skill)
    can_install = current_skill is None or _is_legacy(current_skill, "skill", hashes)
    skill_installed = current_skill == bundled["skill"]

    for prefix in prefixes:
        for logical in (_LEGACY_SKILL_PATH, _SKILL_PATH):
            path = f"{prefix}/{logical}" if prefix else logical
            raw = await stored.adownload_bytes(path)
            if not _is_legacy(raw, "skill", hashes):
                continue
            if can_install and not skill_installed:
                await stored.aupload_bytes(canonical_skill, bundled["skill"])
                changed.append(canonical_skill)
                skill_installed = True
            if path != canonical_skill:
                # Delete just the recognized file, never a folder that could
                # contain additional user-authored documents.
                await stored.adelete(path)
                changed.append(path)

    for kind, logical in (("soul", "SOUL.md"), ("manifest", "manifest.json")):
        for prefix in prefixes:
            path = f"{prefix}/{logical}" if prefix else logical
            raw = await stored.adownload_bytes(path)
            if _is_legacy(raw, kind, hashes):
                await stored.aupload_bytes(path, bundled[kind])
                changed.append(path)
    return changed
