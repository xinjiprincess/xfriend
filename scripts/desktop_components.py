"""Stage and verify pinned desktop engine/browser inputs before packaging."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


def fingerprint(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def component_path(root: Path, name: str) -> Path:
    path = root / name
    if (
        not name
        or PurePosixPath(name).is_absolute()
        or PureWindowsPath(name).drive
        or ".." in PurePosixPath(name).parts
        or "\\" in name
        or not path.resolve().is_relative_to(root.resolve())
        or path.is_symlink()
    ):
        raise ValueError("Invalid desktop component path")
    return path


def verify(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "components.json").read_text(encoding="utf-8"))
    if (
        manifest["format"] != 1
        or manifest["platform"] != "windows-amd64"
        or manifest["application"] != "xfriend"
    ):
        raise ValueError("Unsupported desktop component manifest")
    expected = manifest["sha256"]
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / "components.json"
    }
    if actual != set(expected):
        raise ValueError("Desktop components contain missing or unexpected files")
    for name, digest in expected.items():
        path = component_path(root, name)
        if fingerprint(path) != digest:
            raise ValueError(f"Desktop component checksum mismatch: {name}")
    return dict(manifest)


def stage(dsh: Path, browsers: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    engine = json.loads((dsh / "engine.json").read_text(encoding="utf-8"))
    if (
        engine["id"] != "dsh"
        or len(engine["upstream_commit"]) != 40
        or any(c not in "0123456789abcdef" for c in engine["upstream_commit"])
    ):
        raise ValueError("DSH must declare a pinned upstream commit")
    for name, digest in engine["sha256"].items():
        path = component_path(dsh, name)
        if fingerprint(path) != digest:
            raise ValueError("DSH input checksum mismatch")
    if not all(component_path(dsh, engine[key]).is_file() for key in ("runtime", "entry")):
        raise ValueError("DSH runtime or entry is missing")
    if not list(
        browsers.glob(
            "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"
        )
    ):
        raise ValueError("Pinned Windows export browser is missing")
    for source in (dsh, browsers):
        if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
            raise ValueError("Component inputs must be materialized, without symbolic links")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".components-", dir=output.parent))
    try:
        shutil.copytree(dsh, temporary / "engines" / "dsh")
        shutil.copytree(browsers, temporary / "browsers")
        manifest = {
            "format": 1,
            "application": "xfriend",
            "platform": "windows-amd64",
            "dsh_version": engine["version"],
            "upstream_commit": engine["upstream_commit"],
            "sha256": {
                path.relative_to(temporary).as_posix(): fingerprint(path)
                for path in sorted(temporary.rglob("*"))
                if path.is_file()
            },
        }
        (temporary / "components.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        verify(temporary)
        if output.exists():
            raise FileExistsError(output)
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--dsh", type=Path, required=True)
    stage_parser.add_argument("--browsers", type=Path, required=True)
    stage_parser.add_argument("--output", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    result = (
        stage(args.dsh, args.browsers, args.output)
        if args.operation == "stage"
        else verify(args.root)
    )
    print(
        json.dumps(
            {
                "verified": True,
                "files": len(result["sha256"]),
                "upstream_commit": result["upstream_commit"],
            }
        )
    )
