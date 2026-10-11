"""Failed activations restore prior files; saved versions can be restored."""

from types import SimpleNamespace

import pytest

from octop.infra.agents.plugins import manager as module
from octop.infra.errors import OctopError


def test_plugin_update_rollback_and_failure(tmp_path, monkeypatch):
    manager = module.PluginManager(
        plugins_dir=tmp_path / "plugins", config_path=tmp_path / "config.json"
    )
    source = tmp_path / "source"
    source.mkdir()

    def package(version):
        (source / "plugin.yaml").write_text(f'id: demo\nversion: "{version}"\n', encoding="utf-8")
        (source / "payload").write_text(version, encoding="utf-8")

    monkeypatch.setattr(module, "unload_plugin", lambda plugin_id: None)

    def load(path, **kwargs):
        manifest = module.PluginManifest.load(path / "plugin.yaml")
        if str(manifest.version) == "bad":
            raise ValueError("invalid entrypoint")
        return SimpleNamespace(manifest=manifest)

    monkeypatch.setattr(module, "_load_plugin_dir", load)
    package("1")
    manager.install_path(source)
    package("2")
    manager.install_path(source, force=True)
    saved = manager.versions("demo")
    assert saved[0]["version"] == "1"
    manager.rollback("demo", saved[0]["id"])
    assert (tmp_path / "plugins/demo/payload").read_text() == "1"
    package("bad")
    with pytest.raises(OctopError):
        manager.install_path(source, force=True)
    assert (tmp_path / "plugins/demo/payload").read_text() == "1"
    with pytest.raises(OctopError):
        manager.rollback("demo", "../escape")
