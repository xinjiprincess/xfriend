"""Archive precompiled Python code; native libraries and resources stay on disk.

The launcher reads this archive once and preserves original module file paths,
so extension imports, package resources and metadata retain ordinary semantics.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def build(packages: Path) -> Path:
    output = packages.parent / "startup-code.zip"
    suffix = f".{sys.implementation.cache_tag}.pyc"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for source in sorted(packages.rglob("*.pyc")):
            if source.parent.name != "__pycache__" or not source.name.endswith(suffix):
                continue
            relative = (
                source.parent.parent.relative_to(packages).as_posix()
                + "/"
                + source.name.removesuffix(suffix)
                + ".pyc"
            ).removeprefix("./")
            archive.write(source, relative)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", type=Path)
    print(build(parser.parse_args().packages))
