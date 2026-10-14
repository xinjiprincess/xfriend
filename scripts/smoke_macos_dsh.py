"""Check native bundled DSH without provider secrets or model requests."""

from __future__ import annotations

import argparse
import asyncio
import http.cookiejar
import json
import runpy
import sys
import tempfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace


async def check(runtime: Path) -> None:
    sys.path.insert(0, str(runtime))
    runpy.run_path(str(runtime / "launch.py"))["_bootstrap"](user_dependencies=False)
    from octop.infra.agents.settings.acp import ACPSettingsStore
    from octop.infra.engines.dsh import DSHService
    from octop.infra.engines.dsh_connectors import DSHConnectorBridge
    from octop.infra.engines.dsh_web import DSHWebWorkspace

    features = json.loads((runtime / "desktop-features.json").read_text())
    with tempfile.TemporaryDirectory(prefix="xfriend-macos-dsh-") as directory:
        home = Path(directory)
        values = {"dsh_expert:7": json.dumps({"model": '["stub", "smoke-model"]'})}
        settings = ACPSettingsStore(
            settings_repo=SimpleNamespace(get=values.get, set=values.__setitem__),
            agents_repo=SimpleNamespace(list_by_user=lambda _: []),
            home=home,
        )
        runner = settings.load_runners(7)["dsh"]
        assert bool(runner["enabled"]) == features["dsh"]
        if not features["dsh"]:
            assert not (runtime / "engines/dsh").exists()
            print(json.dumps({"flavor": "standard", "dsh_disabled": True}))
            return
        assert Path(runner["command"]) == runtime / "engines/dsh/node-runtime"
        provider = SimpleNamespace(
            enabled=True, api_key="isolated-smoke-key", base_url="https://example.invalid/v1"
        )
        bridge = DSHConnectorBridge(
            SimpleNamespace(provider_repo=SimpleNamespace(get_by_name=lambda _: provider))
        )
        engine = DSHService(home, settings, bridge)
        web = DSHWebWorkspace(home, settings, engine)
        try:
            session = await engine.create(7, "")
            await engine._open(session)
            assert session.acp_id
            address = await web.open(7)

            def fetch() -> int:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}),
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                )
                with opener.open(address, timeout=10) as response:
                    assert b"<html" in response.read().lower()
                    return int(response.status)

            assert await asyncio.to_thread(fetch) == 200
            print(json.dumps({"flavor": "dsh-slim", "acp_session": "passed", "original_web": 200}))
        finally:
            await web.shutdown()
            await engine.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(check(args.runtime.resolve()))
