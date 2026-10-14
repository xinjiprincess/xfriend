"""Rebase a verified desktop runtime on a new local application wheel."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from slim_desktop_runtime import slim


def prepare(source: Path, wheel: Path, output: Path, resume: bool = False) -> None:
    version = wheel.name.split("-")[1]
    base = output / "base"
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Source and output must be separate trees")
    if output.exists() and not resume:
        raise FileExistsError(output)
    if not resume:
        output.mkdir(parents=True)
        shutil.copytree(source, base)
    elif not (base / "runtime/python.exe").is_file() or not (base / "VERSION.txt").is_file():
        raise ValueError("Cannot resume an incomplete runtime seed")
    packages = base / "packages"
    shutil.rmtree(packages / "octop")
    for metadata in packages.glob("octop-*.dist-info"):
        shutil.rmtree(metadata)
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.namelist():
            if ".." in Path(item).parts or Path(item).is_absolute():
                raise ValueError("Invalid application wheel")
        archive.extractall(packages)
    shutil.copy2(
        source / "packages/octop/infra/licensing/release-profile.json",
        packages / "octop/infra/licensing/release-profile.json",
    )
    marker = base / "VERSION.txt"
    marker.write_text("platform=windows-amd64\noctop_version=" + version + "\n", encoding="utf-8")
    python = base / "runtime/python.exe"
    scripts = Path(__file__).resolve().parent
    subprocess.run(
        [str(python), str(scripts / "prepare_runtime_imports.py"), str(packages)], check=True
    )
    subprocess.run([str(python), str(scripts / "build_catalog_index.py"), str(base)], check=True)
    subprocess.run(
        [
            str(python),
            "-m",
            "compileall",
            "-q",
            "-f",
            "--invalidation-mode",
            "unchecked-hash",
            str(packages / "octop"),
        ],
        check=True,
    )
    subprocess.run(
        [str(python), str(scripts / "build_startup_code_archive.py"), str(packages)], check=True
    )
    reports = []
    for flavor in ("standard", "dsh-slim"):
        reports.append(slim(base, output / flavor, flavor))
    (output / "size-report.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    # Only matching UI assets are needed to compile the installed-runtime shell.
    with zipfile.ZipFile(output / "dashboard.zip", "w", zipfile.ZIP_DEFLATED) as ui:
        for path in (packages / "octop/dashboard").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                ui.write(path, "Xfriend/" + path.relative_to(base).as_posix())
        for path in (marker, packages / "octop/infra/licensing/release-profile.json"):
            ui.write(path, "Xfriend/" + path.relative_to(base).as_posix())
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    prepare(args.source.resolve(), args.wheel.resolve(), args.output.resolve(), args.resume)
