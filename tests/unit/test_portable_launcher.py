from __future__ import annotations

import json
import os
import runpy
import shutil
import site
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.mark.parametrize("desktop", [False, True])
def test_installed_launcher_keeps_plugin_installs_in_user_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: bool
) -> None:
    root = tmp_path / "application"
    (root / "packages").mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "desktop/portable/templates/launch.py"
    launcher = root / "launch.py"
    shutil.copyfile(source, launcher)
    home = tmp_path / "user-data"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("XFRIEND_HOME", str(home))
    monkeypatch.setenv("XFRIEND_DESKTOP_RUNTIME", "1" if desktop else "0")
    monkeypatch.setenv("PIP_TARGET", "previous-target")
    monkeypatch.setenv("PYTHONPATH", "previous-pythonpath")
    # Isolate .pth handling from the test interpreter itself.
    added: list[str] = []
    monkeypatch.setattr(site, "addsitedir", added.append)
    runpy.run_path(str(launcher))["_bootstrap"]()
    if desktop:
        dependency_dir = home / "python-dependencies"
        assert dependency_dir.is_dir()
        assert str(dependency_dir) in added
        assert os.environ["PIP_TARGET"] == str(dependency_dir)
        assert os.environ["PYTHONPATH"].split(os.pathsep) == [
            str(root / "packages"),
            str(dependency_dir),
        ]
    else:
        assert os.environ["PIP_TARGET"] == "previous-target"
        assert os.environ["PYTHONPATH"] == "previous-pythonpath"
        assert not home.exists()


def test_prewarm_waits_for_identity_and_reuses_process(tmp_path: Path) -> None:
    root = tmp_path / "application"
    packages = root / "packages"
    (packages / "octop").mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "desktop/portable/templates/launch.py"
    shutil.copyfile(source, root / "launch.py")
    for name in ("fastapi.py", "uvicorn.py", "octop/__init__.py"):
        (packages / name).write_text("", encoding="utf-8")
    (packages / "octop/__main__.py").write_text(
        "import os, pathlib\n"
        "home = pathlib.Path(os.environ['XFRIEND_HOME'])\n"
        "home.mkdir(parents=True, exist_ok=True)\n"
        "(home / 'identity-ready').write_text(str(os.getpid()))\n",
        encoding="utf-8",
    )
    home = tmp_path / "account-space"
    child = subprocess.Popen(
        [sys.executable, "-S", str(root / "launch.py"), "--prewarm"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        assert child.stdout is not None
        ready = executor.submit(child.stdout.readline).result(timeout=10)
        marker, process_id = ready.strip().split()
        assert marker == "xfriend-prewarm-ready"
        assert not home.exists()
        assert child.poll() is None
        _, error = child.communicate(
            json.dumps({"port": 18088, "environment": {"XFRIEND_HOME": str(home)}}) + "\n",
            timeout=10,
        )
        assert child.returncode == 0, error
        assert (home / "identity-ready").read_text() == process_id
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
        executor.shutdown(wait=True)
