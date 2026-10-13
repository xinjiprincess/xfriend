from __future__ import annotations

import ast
import compileall
import importlib.metadata
import py_compile
import subprocess
import sys
import types
from pathlib import Path

import pytest
from scripts.build_startup_code_archive import build
from scripts.prepare_runtime_imports import RELATIVE, prepare, transform


def test_provider_capabilities_accept_classes_loaded_after_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        importlib.metadata.distribution("deepagents")
        .locate_file(RELATIVE)
        .read_text(encoding="utf-8")
    )
    tree = ast.parse(transform(source))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_model_tolerates_non_pdf_files"
    )
    namespace = {"sys": sys}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "capability-test", "exec"), namespace
    )
    check = namespace["_model_tolerates_non_pdf_files"]
    for module in ("langchain_openai", "langchain_google_genai"):
        monkeypatch.delitem(sys.modules, module, raising=False)
    assert not check(object())

    class Model:
        pass

    class Child(Model):
        pass

    monkeypatch.setitem(
        sys.modules, "langchain_google_genai", types.SimpleNamespace(ChatGoogleGenerativeAI=Model)
    )
    assert check(Model())
    assert check(Child())
    assert not check(None)


def test_import_patch_refuses_unreviewed_dependency(tmp_path: Path) -> None:
    source = tmp_path / RELATIVE
    source.parent.mkdir(parents=True)
    source.write_text("# new dependency version")
    with pytest.raises(ValueError, match="source changed"):
        prepare(tmp_path)
    assert source.read_text() == "# new dependency version"


def test_precompiled_code_archive_imports_packages_and_namespaces(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    for name in ("langchain", "langgraph"):
        directory = packages / name
        directory.mkdir(parents=True)
        if name != "langgraph":
            (directory / "__init__.py").write_text("value = 42\n")
        (directory / "probe.py").write_text("value = 42\n")
    assert compileall.compile_dir(
        packages, quiet=2, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH
    )
    (packages / "langchain" / "data.txt").write_text("resource content")
    archive = build(packages)
    loader = Path(__file__).resolve().parents[2] / "desktop/portable/templates"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[2]);sys.path.insert(0,sys.argv[3]);import startup_archive;startup_archive.install(Path(sys.argv[1]),Path(sys.argv[3]));import langchain,langgraph.probe;assert langchain.value==42;assert langgraph.probe.value==42;assert '.zip' not in langchain.__file__;from importlib.resources import files;assert files(langchain).joinpath('data.txt').read_text()=='resource content';assert isinstance(langchain.__loader__,startup_archive.MemoryLoader)",
            str(archive),
            str(loader),
            str(packages),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
