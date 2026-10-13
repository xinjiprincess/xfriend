"""Materialize the pinned native DSH carrier before the common component gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from prepare_dsh_web_component import prepare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--closure", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=args.upstream, text=True
    ).strip()
    if commit != "d743267388641bc76f17c45ce8b4c231aed1d32c":
        raise ValueError("Unexpected DSH source revision")
    base = args.output.with_name(args.output.name + "-base")
    if base.exists() or args.output.exists():
        raise FileExistsError("Choose fresh component output directories")
    source = args.upstream / "python/sdk-runtime/src/deepseek_harness_runtime/runtime"
    shutil.copytree(source, base, symlinks=False)
    node = Path(shutil.which("node") or "").resolve()
    target = base / "node-runtime"
    shutil.copy2(node, target)
    target.chmod(0o755)
    version = json.loads((args.upstream / "package.json").read_text())["version"]
    entries = ("node-runtime", "node/runtime-bootstrap.mjs")
    manifest = {
        "id": "dsh",
        "version": version,
        "upstream_commit": commit,
        "transport": "acp",
        "entry": entries[1],
        "runtime": entries[0],
        "capabilities": ["expert-session", "resume", "cancel", "permission"],
        "sha256": {
            name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in entries
        },
    }
    (base / "engine.json").write_text(json.dumps(manifest, indent=2))
    prepare(base, args.upstream, args.closure, args.output)


if __name__ == "__main__":
    main()
