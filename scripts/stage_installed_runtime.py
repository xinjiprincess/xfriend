"""Stage the complete Windows runtime for NSIS, never for first-run extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath


def stage(archive: Path, output: Path) -> Path:
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        versions = [item for item in bundle.infolist() if item.filename.endswith("/VERSION.txt")]
        if len(versions) != 1:
            raise ValueError("Expected one runtime VERSION.txt")
        match = re.search(r"(?m)^octop_version=(\S+)$", bundle.read(versions[0]).decode().strip())
        if not match or not re.fullmatch(r"\d+\.\d+\.\d+[a-zA-Z0-9.+-]*", match[1]):
            raise ValueError("Invalid runtime version")
        version = match[1]
        prefix = versions[0].filename.removesuffix("VERSION.txt")
        target = output / "installed-runtime" / version
        marker = target / "archive.sha256"
        if target.exists():
            if not marker.is_file() or marker.read_text() != digest:
                raise ValueError("Runtime version already staged from a different archive")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                staging = Path(temporary) / "runtime"
                staging.mkdir()
                for item in bundle.infolist():
                    if not item.filename.startswith(prefix):
                        raise ValueError("Unexpected archive root")
                    relative = item.filename[len(prefix) :]
                    if not relative:
                        continue
                    parts = PurePosixPath(relative)
                    if (
                        parts.is_absolute()
                        or ".." in parts.parts
                        or "\\" in relative
                        or ":" in relative
                    ):
                        raise ValueError("Unsafe runtime archive path")
                    destination = staging / relative
                    if item.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.open(item) as source, destination.open("wb") as sink:
                            shutil.copyfileobj(source, sink)
                for required in (
                    "launch.py",
                    "runtime/python.exe",
                    "packages/octop/dashboard/index.html",
                ):
                    if not (staging / required).is_file():
                        raise ValueError(f"Missing installed runtime file: {required}")
                (staging / "archive.sha256").write_text(digest)
                # Windows scanners can briefly hold freshly extracted directories.
                for attempt in range(21):
                    try:
                        staging.rename(target)
                        break
                    except PermissionError as exc:
                        if getattr(exc, "winerror", None) not in (5, 32) or attempt == 20:
                            raise
                        time.sleep(1)
    (output / "runtime-release.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portable", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    print(stage(arguments.portable, arguments.output))
