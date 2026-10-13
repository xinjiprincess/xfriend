"""Add the pinned upstream DSH web workspace to an ACP-capable desktop component."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def prepare(dsh: Path, upstream: Path, web_closure: Path, output: Path) -> None:
    if output.exists():
        raise FileExistsError(output)
    engine = json.loads((dsh / "engine.json").read_text(encoding="utf-8"))
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip()
    if commit != engine["upstream_commit"]:
        raise ValueError("DSH source does not match the pinned component commit")
    web_package = json.loads((web_closure / "package.json").read_text(encoding="utf-8"))
    if web_package.get("version") != engine["version"]:
        raise ValueError("DSH web closure version does not match the component")
    web_dist = upstream / "apps" / "web" / "dist"
    if not (web_dist / "index.html").is_file():
        raise FileNotFoundError("Build the pinned DSH web frontend first")

    packages: dict[str, Path] = {}
    for folder in ("packages", "apps", "vendor"):
        manifests = list((upstream / folder).glob("*/package.json"))
        manifests.extend((upstream / folder).glob("*/*/package.json"))
        for manifest in manifests:
            try:
                name = json.loads(manifest.read_text(encoding="utf-8")).get("name")
            except (OSError, ValueError):
                continue
            if isinstance(name, str):
                packages[name] = manifest.parent

    try:
        shutil.copytree(dsh, output)
        modules = output / "node" / "node_modules"
        scope = modules / "@deepseek-ai"
        source_scope = web_closure / "node_modules" / "@deepseek-ai"
        for source in source_scope.iterdir():
            target = scope / source.name
            if not target.exists():
                shutil.copytree(source, target)

        pending = [modules]
        seen: set[Path] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            current_scope = current / "@deepseek-ai"
            if not current_scope.is_dir():
                continue
            for target in current_scope.iterdir():
                if not target.is_dir():
                    continue
                source = packages.get(f"@deepseek-ai/{target.name}")
                if source is not None and (source / "lib").is_dir():
                    shutil.copytree(source / "lib", target / "lib", dirs_exist_ok=True)
                if target.name == "dsh-web-frontend":
                    shutil.copytree(web_dist, target / "dist", dirs_exist_ok=True)
                nested = target / "node_modules"
                if nested.is_dir():
                    pending.append(nested)

        required = (
            scope / "dsh-web-frontend" / "dist" / "index.html",
            scope / "dsh-client-modules" / "lib" / "index.js",
        )
        if not all(path.is_file() for path in required):
            raise FileNotFoundError("DSH web workspace dependencies are incomplete")
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsh", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--web-closure", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.dsh, args.upstream, args.web_closure, args.output)
