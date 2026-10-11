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


def test_staging_rejects_modified_and_unlisted_inputs(tmp_path: Path) -> None:
    dsh = tmp_path / "dsh"
    dsh.mkdir()
    (dsh / "node.exe").write_bytes(b"node")
    (dsh / "entry.js").write_bytes(b"entry")
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
    browser = browsers / "chromium_headless_shell-1" / "chrome-headless-shell-win64"
    browser.mkdir(parents=True)
    (browser / "chrome-headless-shell.exe").write_bytes(b"browser")
    output = tmp_path / "release"
    manifest = components.stage(dsh, browsers, output)
    assert components.verify(output) == manifest
    with pytest.raises(FileExistsError):
        components.stage(dsh, browsers, output)
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
        components.stage(dsh, browsers, tmp_path / "unsafe")
    assert not (tmp_path / "unsafe").exists()
