"""Refresh one corrected Python module in already prepared release variants."""

import argparse
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def refresh(root: Path, source: Path, relative: str) -> None:
    target = root / "packages" / relative
    if not target.is_file() or ".." in Path(relative).parts or Path(relative).is_absolute():
        raise ValueError("Module is not part of this runtime")
    shutil.copy2(source, target)
    with tempfile.TemporaryDirectory() as directory:
        bytecode = Path(directory) / "module.pyc"
        subprocess.run(
            [
                str(root / "runtime/python.exe"),
                "-c",
                "import py_compile,sys; py_compile.compile(sys.argv[1],cfile=sys.argv[2],doraise=True,invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)",
                str(target),
                str(bytecode),
            ],
            check=True,
        )
        entry = relative.removesuffix(".py") + ".pyc"
        archive = root / "startup-code.zip"
        temporary = archive.with_suffix(".zip.next")
        with (
            zipfile.ZipFile(archive) as old,
            zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as new,
        ):
            if entry not in old.namelist():
                raise ValueError("Module is missing from the startup archive")
            for info in old.infolist():
                new.writestr(
                    info,
                    bytecode.read_bytes() if info.filename == entry else old.read(info.filename),
                )
        temporary.replace(archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--relative", required=True)
    args = parser.parse_args()
    for item in args.root:
        refresh(item.resolve(), args.source.resolve(), args.relative)
