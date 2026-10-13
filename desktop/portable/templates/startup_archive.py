"""Load packaged bytecode from one memory buffer, retaining real resource paths."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import marshal
import sys
import zipfile
from pathlib import Path


class MemoryLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, fullname: str, source: str, archive: zipfile.ZipFile, entry: str):
        super().__init__(fullname, source)
        self.archive = archive
        self.entry = entry

    def get_code(self, fullname: str):
        bytecode = self.archive.read(self.entry)
        if bytecode[:4] != importlib.util.MAGIC_NUMBER:
            raise ImportError("Bundled bytecode uses a different Python version")
        return marshal.loads(bytecode[16:])


class MemoryFinder:
    def __init__(self, archive_path: Path, packages: Path):
        self.archive = zipfile.ZipFile(io.BytesIO(archive_path.read_bytes()))
        self.packages = packages
        self.modules = {}
        for entry in self.archive.namelist():
            if not entry.endswith(".pyc"):
                continue
            stem = entry[:-4]
            package = stem.endswith("/__init__")
            name = stem.removesuffix("/__init__").replace("/", ".")
            self.modules[name] = (entry, package)

    def find_spec(self, fullname, path=None, target=None):
        item = self.modules.get(fullname)
        if item is None:
            return None
        entry, package = item
        source = str(self.packages / (entry[:-1]))
        loader = MemoryLoader(fullname, source, self.archive, entry)
        return importlib.util.spec_from_file_location(
            fullname,
            source,
            loader=loader,
            submodule_search_locations=[str(Path(source).parent)] if package else None,
        )


def install(archive_path: Path, packages: Path):
    finder = MemoryFinder(archive_path, packages)
    sys.meta_path.insert(0, finder)
    return finder
