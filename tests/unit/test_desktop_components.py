"""Release components are pinned, complete and verified before packaging."""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "desktop_components", Path(__file__).parents[2] / "scripts" / "desktop_components.py"
)
assert _spec is not None and _spec.loader is not None
components = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(components)


@pytest.mark.parametrize("platform", ["windows-amd64", "darwin-arm64"])
def test_staging_rejects_modified_and_unlisted_inputs(tmp_path: Path, platform: str) -> None:
    dsh = tmp_path / "dsh"
    dsh.mkdir()
    (dsh / "node.exe").write_bytes(b"node")
    (dsh / "entry.js").write_bytes(b"entry")
    for name in (
        "node/node_modules/@deepseek-ai/dsh-web-frontend/dist/index.html",
        "node/node_modules/@deepseek-ai/dsh-client-modules/lib/index.js",
    ):
        asset = dsh / name
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"asset")
    engine = {
        "id": "dsh",
        "upstream_commit": "a" * 40,
        "version": "test",
        "runtime": "node.exe",
        "entry": "entry.js",
        "sha256": {name: components.fingerprint(dsh / name) for name in ("node.exe", "entry.js")},
    }
    (dsh / "engine.json").write_text(json.dumps(engine), encoding="utf-8")
    browsers = tmp_path / "browsers"
    browser_dir, browser_name = (
        ("chrome-headless-shell-win64", "chrome-headless-shell.exe")
        if platform == "windows-amd64"
        else ("chrome-headless-shell-mac-arm64", "headless_shell")
    )
    browser = browsers / "chromium_headless_shell-1" / browser_dir
    browser.mkdir(parents=True)
    (browser / browser_name).write_bytes(b"browser")
    output = tmp_path / "release"
    manifest = components.stage(dsh, browsers, output, platform)
    assert manifest["platform"] == platform
    assert components.verify(output) == manifest
    with pytest.raises(FileExistsError):
        components.stage(dsh, browsers, output, platform)
    (output / "engines" / "dsh" / "entry.js").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        components.verify(output)
    (output / "engines" / "dsh" / "entry.js").write_bytes(b"entry")
    (output / "unexpected.exe").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="unexpected"):
        components.verify(output)
    engine["runtime"] = "../outside.exe"
    (dsh / "engine.json").write_text(json.dumps(engine), encoding="utf-8")
    with pytest.raises(ValueError, match="path"):
        components.stage(dsh, browsers, tmp_path / "unsafe", platform)
    assert not (tmp_path / "unsafe").exists()
