from pathlib import Path
from zipfile import ZipFile

import pytest
from scripts import stage_installed_runtime
from scripts.stage_installed_runtime import stage


def archive(path: Path, extra: str | None = None) -> None:
    with ZipFile(path, "w") as bundle:
        bundle.writestr("Xfriend/VERSION.txt", "octop_version=1.4.4\n")
        for name in ("launch.py", "runtime/python.exe", "packages/octop/dashboard/index.html"):
            bundle.writestr("Xfriend/" + name, "test")
        if extra:
            bundle.writestr(extra, "bad")


def test_installer_stages_complete_runtime_once(tmp_path: Path) -> None:
    source = tmp_path / "portable.zip"
    archive(source)
    output = tmp_path / "bundled"
    root = stage(source, output)
    assert root == output / "installed-runtime" / "1.4.4"
    assert (root / "runtime/python.exe").read_text() == "test"
    assert stage(source, output) == root
    archive(source, "Xfriend/extra.txt")
    with pytest.raises(ValueError, match="different archive"):
        stage(source, output)


@pytest.mark.parametrize("name", ["Xfriend/../escape", "other/file", "Xfriend/C:/escape"])
def test_installer_rejects_unsafe_archive(tmp_path: Path, name: str) -> None:
    source = tmp_path / "portable.zip"
    archive(source, name)
    with pytest.raises(ValueError):
        stage(source, tmp_path / "bundled")
    assert not (tmp_path / "bundled/runtime-release.json").exists()


def test_installer_retries_windows_scanner_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "portable.zip"
    archive(source)
    rename = Path.rename
    calls = 0

    def locked_once(path: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 1:
            error = PermissionError("temporarily locked")
            error.winerror = 5
            raise error
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", locked_once)
    monkeypatch.setattr(stage_installed_runtime.time, "sleep", lambda _: None)
    root = stage(source, tmp_path / "bundled")
    assert calls == 2
    assert (root / "runtime/python.exe").read_text() == "test"
