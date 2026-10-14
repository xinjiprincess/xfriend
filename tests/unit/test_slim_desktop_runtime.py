import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


@pytest.mark.parametrize("flavor", ["standard", "dsh-slim"])
def test_release_variant_preserves_runtime_sources_and_licenses(
    tmp_path: Path, flavor: str
) -> None:
    spec = importlib.util.spec_from_file_location(
        "slim", Path(__file__).parents[2] / "scripts/slim_desktop_runtime.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "source"
    files = {
        "packages/pkg/__pycache__/code.cpython-312.pyc": b"bytecode",
        "packages/pkg/code.py": b"source",
        "engines/dsh/node/node_modules/@deepseek-ai/libreoffice-kit-win32-x64/bin/office.exe": b"office",
        "engines/dsh/node/node_modules/pkg/lib/index.js": b"runtime",
        "engines/dsh/node/node_modules/pkg/lib/index.js.map": b"map",
        "engines/dsh/node/node_modules/pkg/LICENSE": b"license",
    }
    for name, data in files.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with zipfile.ZipFile(source / "startup-code.zip", "w") as z:
        z.writestr("pkg/code.pyc", b"bytecode")
    output = tmp_path / "result"
    report = module.slim(source, output, flavor)
    assert (source / "packages/pkg/__pycache__/code.cpython-312.pyc").exists()
    assert not (output / "packages/pkg/__pycache__/code.cpython-312.pyc").exists()
    assert (output / "packages/pkg/code.py").read_bytes() == b"source"
    assert (output / "engines/dsh").exists() == (flavor == "dsh-slim")
    if flavor == "dsh-slim":
        assert (output / "engines/dsh/node/node_modules/pkg/LICENSE").is_file()
        assert not list(output.rglob("*.exe"))
    assert json.loads((output / "desktop-features.json").read_text())["flavor"] == flavor
    assert report["removed_files"] >= 2
