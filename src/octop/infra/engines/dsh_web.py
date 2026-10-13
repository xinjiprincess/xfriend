"""Start the pinned upstream DSH web workspace only when its page is opened."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from octop.infra.agents.settings.acp import ACPSettingsStore
from octop.infra.engines.runtime import runner_identity

_URL = re.compile(r"dsh web: (http://[^\s]+)")


class DSHWebWorkspace:
    def __init__(self, home: Path, settings: ACPSettingsStore) -> None:
        self.home = home
        self.settings = settings
        self._lock = asyncio.Lock()
        self._running: dict[int, tuple[asyncio.subprocess.Process, str, asyncio.Task[None]]] = {}

    async def open(self, user_id: int) -> str:
        async with self._lock:
            current = self._running.get(user_id)
            if current is not None and current[0].returncode is None:
                return current[1]
            if current is not None:
                current[2].cancel()
                self._running.pop(user_id, None)

            runner = await asyncio.to_thread(self.settings.load_runners, user_id)
            config = runner["dsh"]
            node = Path(str(config["command"])).resolve()
            cli = node.parent / "node" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
            web = (
                node.parent
                / "node"
                / "node_modules"
                / "@deepseek-ai"
                / "dsh-web-frontend"
                / "dist"
                / "index.html"
            )
            if not node.is_file() or not cli.is_file() or not web.is_file():
                raise FileNotFoundError("The active DSH package has no original web workspace")
            signature = await asyncio.to_thread(runner_identity, config)
            data_dir = (
                self.home
                / "engine-data"
                / "dsh"
                / str(user_id)
                / "runtime"
                / signature["signature"]
            )
            await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True)
            env = {
                **os.environ,
                **config.get("env", {}),
                "DSH_HOME": str(data_dir),
                "DSH_TELEMETRY_DISABLED": "1",
            }
            process = await asyncio.create_subprocess_exec(
                str(node),
                str(cli),
                "web",
                "--no-open",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                cwd=str(data_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert process.stdout is not None
            try:
                async with asyncio.timeout(90):
                    while line := await process.stdout.readline():
                        match = _URL.search(line.decode("utf-8", errors="replace"))
                        if match is None:
                            continue
                        address = match.group(1)
                        parsed = urlsplit(address)
                        if parsed.hostname != "127.0.0.1" or parsed.port is None:
                            raise ValueError("DSH web returned a non-loopback address")
                        drain = asyncio.create_task(self._drain(process), name="dsh-web-output")
                        self._running[user_id] = process, address, drain
                        return address
                    raise RuntimeError("DSH web exited before it became ready")
            except BaseException:
                await self._stop(process)
                raise

    async def _drain(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while await process.stdout.read(8192):
            pass

    async def _stop(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def shutdown(self) -> None:
        async with self._lock:
            running, self._running = self._running, {}
            for process, _address, drain in running.values():
                await self._stop(process)
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)
