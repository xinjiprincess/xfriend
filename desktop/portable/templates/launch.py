"""Green portable entry: wire packages/ onto sys.path then run ``octop``.

Using plain ``PYTHONPATH=packages`` skips ``.pth`` processing (pywin32 etc.).
``site.addsitedir`` loads those hooks. On Windows we also expose
``pywin32_system32`` DLLs so ``import pywintypes`` works.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import runpy
import site
import sys
from pathlib import Path


_archive_installed = False


def _bootstrap(*, user_dependencies: bool = True) -> Path:
    global _archive_installed
    root = Path(__file__).resolve().parent
    # The bundled interpreter is launched by absolute path, so its bin dir is
    # rarely on PATH. Prepend it so inherited envs (agent shells, subprocesses)
    # resolve ``python3`` to the same interpreter Octop runs with.
    interpreter_dir = os.path.dirname(sys.executable)
    if interpreter_dir:
        _path = os.environ.get("PATH", "")
        if interpreter_dir not in _path.split(os.pathsep):
            os.environ["PATH"] = (
                f"{interpreter_dir}{os.pathsep}{_path}" if _path else interpreter_dir
            )
    packages = root / "packages"
    if not packages.is_dir():
        sys.stderr.write(f"launch.py: packages/ missing next to {root}\n")
        raise SystemExit(1)

    # Process .pth files (required for pywin32 layout).
    site.addsitedir(str(packages))

    if (
        user_dependencies
        and sys.platform == "win32"
        and os.environ.get("XFRIEND_DESKTOP_RUNTIME") == "1"
    ):
        # Installed runtimes live in Program Files. Plugin dependencies must be
        # writable per user, without modifying the immutable application code.
        home = Path(
            os.environ.get("XFRIEND_HOME")
            or os.environ.get("OCTOP_HOME")
            or Path.home() / ".xfriend"
        )
        dependencies = home / "python-dependencies"
        dependencies.mkdir(parents=True, exist_ok=True)
        site.addsitedir(str(dependencies))
        os.environ["PIP_TARGET"] = str(dependencies)
        os.environ["PYTHONPATH"] = os.pathsep.join((str(packages), str(dependencies)))

    # Pure Python execution packages are precompiled into one seekable archive.
    # Native modules, metadata and package data retain their ordinary disk paths.
    code_archive = root / "startup-code.zip"
    if not _archive_installed and code_archive.is_file():
        import startup_archive

        startup_archive.install(code_archive, packages)
        _archive_installed = True

    if sys.platform == "win32":
        dll_dir = packages / "pywin32_system32"
        if dll_dir.is_dir():
            dll_s = str(dll_dir)
            path = os.environ.get("PATH", "")
            if dll_s.lower() not in path.lower():
                os.environ["PATH"] = dll_s + os.pathsep + path
            # Python 3.8+: prefer explicit DLL search path.
            add_dll = getattr(os, "add_dll_directory", None)
            if add_dll is not None:
                with contextlib.suppress(OSError):
                    add_dll(dll_s)
    return root


def main() -> None:
    if sys.argv[1:] == ["--prewarm"]:
        # Load only shared libraries. Account homes, databases and listeners are
        # initialized after the trusted desktop bridge supplies an identity.
        _bootstrap(user_dependencies=False)
        for module in ("fastapi", "uvicorn", "sqlite3"):
            importlib.import_module(module)
        print(f"xfriend-prewarm-ready {os.getpid()}", flush=True)
        line = sys.stdin.readline(16385)
        if not line or len(line) > 16384:
            return
        activation = json.loads(line)
        os.environ.update(activation["environment"])
        sys.argv = [sys.argv[0], "run", "--host", "127.0.0.1", "--port", str(activation["port"])]
    for name, value in tuple(os.environ.items()):
        if name.startswith("XFRIEND_"):
            os.environ["OCTOP_" + name.removeprefix("XFRIEND_")] = value
    _bootstrap()
    # ``python launch.py …`` → same as ``python -m octop …``
    sys.argv = [sys.argv[0], *sys.argv[1:]]
    runpy.run_module("octop", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
