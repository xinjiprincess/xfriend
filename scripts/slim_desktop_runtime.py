"""Build a release variant without changing the verified input runtime."""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path


def slim(source: Path, output: Path, flavor: str) -> dict[str, object]:
    if flavor not in {"standard", "dsh-slim"} or output.exists():
        raise ValueError("Choose a supported variant and a new output directory")
    removed_bytes = removed_files = 0
    archived: set[str] = set()
    archive = source / "startup-code.zip"
    if archive.is_file():
        with zipfile.ZipFile(archive) as code:
            archived = set(code.namelist())

    def excluded(directory: str, names: list[str]) -> list[str]:
        nonlocal removed_bytes, removed_files
        skipped = []
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(source)
            parts = relative.parts
            skip = flavor == "standard" and parts == ("engines", "dsh")
            if flavor == "dsh-slim" and parts[:2] == ("engines", "dsh"):
                skip = (
                    name.startswith("libreoffice-kit-")
                    or name.endswith((".map", ".d.ts"))
                    or name.lower() in {"readme.md", "changelog.md"}
                )
            if (
                parts[0] == "packages"
                and path.parent.name == "__pycache__"
                and name.endswith(".pyc")
            ):
                stem = name.split(".cpython-")[0]
                entry = (
                    path.parent.parent.relative_to(source / "packages") / (stem + ".pyc")
                ).as_posix()
                skip = entry in archived
            if skip:
                skipped.append(name)
                for item in path.rglob("*") if path.is_dir() else [path]:
                    if item.is_file():
                        removed_bytes += item.stat().st_size
                        removed_files += 1
        return skipped

    shutil.copytree(source, output, ignore=excluded, symlinks=True)
    # Empty bytecode and development directories do not need installer entries.
    for directory in sorted(
        (p for p in output.rglob("*") if p.is_dir() and not p.is_symlink()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
    # Preserve a checksum gate after removing only release-excluded resources.
    manifest = output / "components.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text("utf-8"))
        data["sha256"] = {
            name: digest for name, digest in data["sha256"].items() if (output / name).is_file()
        }
        data["flavor"] = flavor
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    files = [p for p in output.rglob("*") if p.is_file()]
    features = {"format": 1, "flavor": flavor, "dsh": flavor == "dsh-slim", "dsh_office": False}
    (output / "desktop-features.json").write_text(json.dumps(features), encoding="utf-8")
    report: dict[str, object] = {
        **features,
        "files": len(files) + 1,
        "bytes": sum(p.stat().st_size for p in files),
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
    }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--flavor", choices=("standard", "dsh-slim"), required=True)
    args = parser.parse_args()
    print(json.dumps(slim(args.source, args.output, args.flavor)))
