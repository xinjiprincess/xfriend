"""Measure real packaged HTTP/login/expert readiness in an isolated user home.

The optional provider database is read-only. Only provider configuration is
copied into the disposable database; existing agents/workspaces are untouched.
Timing begins at process creation, excluding fixture setup. This does not
measure WebView painting or guarantee remote model latency.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SEED = r"""
import asyncio, json, os, runpy, sqlite3, sys
from pathlib import Path
runpy.run_path(sys.argv[1])['_bootstrap']()
from octop.infra.server import OctopServer
async def main():
    home = Path(os.environ['XFRIEND_HOME'])
    (home/'config.json').write_text(json.dumps({'require_setup_password':False,'history_v2_enabled':True}),encoding='utf-8')
    (home/'xfriend.db').touch()
    server = OctopServer(home)
    await server.start(defer_agents=True)
    try:
        user = await server.user_manager.create(username='startup-benchmark',password='BenchmarkPass12!',role='admin')
        if len(sys.argv)>2:
            with sqlite3.connect(Path(sys.argv[2]).resolve().as_uri()+'?mode=ro',uri=True) as conn:
                conn.row_factory=sqlite3.Row
                providers=conn.execute('SELECT * FROM providers WHERE enabled=1').fetchall()
            refs=[]
            for row in providers:
                server.services.provider_repo.create(**{key:row[key] for key in ('name','kind','base_url','api_key','extra_json','models_json')})
                for model in json.loads(row['models_json'] or '[]'):
                    if model.get('enabled',True) and model.get('id'):
                        refs.append(row['name']+'/'+model['id'])
            if not refs: raise RuntimeError('No enabled provider/model for expert benchmark')
            selected=os.environ.get('XFRIEND_BENCHMARK_MODEL') or refs[0]
            if selected not in refs: raise RuntimeError('Selected benchmark model is not configured')
            server.services.agent_repo.create(agent_id='startup-expert',name='Startup benchmark',user_id=user.id,default_model=selected,system_prompt='Reply briefly. Do not call tools.')
            server.services.agent_repo.set_state('startup-expert','running')
            if os.environ.get('BENCHMARK_EXISTING_WORKSPACE') == '1':
                server.start_background_services()
                await server._background_startup_task
    finally: await server.stop()
asyncio.run(main())
"""


def request(
    base: str, path: str, token: str | None = None, body: dict[str, str] | None = None
) -> dict:
    headers = {"Content-Type": "application/json", "Accept-Language": "zh"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        base + path, headers=headers, data=json.dumps(body).encode() if body else None
    )
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.load(response)


def verify_chat(base: str, token: str, start: float) -> dict:
    from websockets.sync.client import connect

    result = {"network_model_called": True, "chat_completed": False}
    began = time.monotonic()
    try:
        with connect(
            base.replace("http://", "ws://") + "/api/agents/startup-expert/chat/ws?token=" + token,
            origin=base,
            open_timeout=5,
            close_timeout=3,
        ) as connection:
            connection.send(
                json.dumps(
                    {
                        "type": "user_turn",
                        "text": "只回复：启动验收成功。不要调用工具。",
                        "reasoning_mode": "disabled",
                    }
                )
            )
            while time.monotonic() - began < 30:
                frame = json.loads(
                    connection.recv(timeout=max(0.1, 30 - (time.monotonic() - began)))
                )
                if frame.get("type") == "error":
                    result["chat_error"] = True
                    break
                if (
                    frame.get("type") == "token"
                    and frame.get("content")
                    and "first_token_seconds" not in result
                ):
                    result["first_token_seconds"] = round(time.monotonic() - start, 3)
                    result["send_to_first_token_seconds"] = round(time.monotonic() - began, 3)
                if frame.get("type") == "done":
                    result["chat_completed"] = "first_token_seconds" in result
                    break
    except Exception as exc:
        result["chat_exception_type"] = type(exc).__name__
    return result


def sample(
    runtime: Path,
    output: Path,
    index: int,
    provider_db: Path | None,
    model: str | None,
    check_chat: bool,
    existing_workspace: bool = False,
) -> dict:
    python = runtime / "runtime/python.exe"
    if not python.exists():
        python = runtime / "runtime/bin/python3"
    with tempfile.TemporaryDirectory(prefix="xfriend-startup-benchmark-") as temporary:
        env = {
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("OCTOP_", "XFRIEND_"))
            },
            "XFRIEND_HOME": temporary,
            "OCTOP_HOME": temporary,
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
            "XFRIEND_DESKTOP_RUNTIME": "1",
        }
        env.pop("XFRIEND_LICENSE_PROFILE", None)
        env.pop("OCTOP_LICENSE_PROFILE", None)
        if model:
            env["XFRIEND_BENCHMARK_MODEL"] = model
        env["BENCHMARK_EXISTING_WORKSPACE"] = "1" if existing_workspace else "0"
        args = [str(python), "-c", SEED, str(runtime / "launch.py")]
        if provider_db:
            args.append(str(provider_db))
        # Fixture output can contain private provider diagnostics. Keep it local.
        with (output / f"seed-{index}.log").open("w", encoding="utf-8") as seed_log:
            subprocess.run(
                args,
                env=env,
                cwd=runtime,
                stdout=seed_log,
                stderr=seed_log,
                check=True,
                timeout=120,
            )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        result: dict = {
            "sample": index,
            "expert_configured": provider_db is not None,
            "includes_webview": False,
            "network_model_called": False,
            "existing_workspace": existing_workspace,
        }
        with (output / f"service-{index}.log").open("w", encoding="utf-8") as log:
            start = time.monotonic()
            process = subprocess.Popen(
                [
                    str(python),
                    str(runtime / "launch.py"),
                    "run",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=runtime,
                env=env,
                stdout=log,
                stderr=log,
            )
            token = None
            try:
                while time.monotonic() - start < 120:
                    if process.poll() is not None:
                        result["process_exit"] = process.returncode
                        break
                    try:
                        health = request(base, "/api/health")
                        if health["core_ready"] and token is None:
                            result["core_http_seconds"] = round(time.monotonic() - start, 3)
                            login = request(
                                base,
                                "/api/auth/login",
                                body={
                                    "username": "startup-benchmark",
                                    "password": "BenchmarkPass12!",
                                },
                            )
                            token = login["access_token"]
                            result["login_seconds"] = round(time.monotonic() - start, 3)
                        if health["runtime_ready"] and "runtime_seconds" not in result:
                            result["runtime_seconds"] = round(time.monotonic() - start, 3)
                        if token and provider_db:
                            agents = request(base, "/api/agents", token)
                            expert = next(
                                (row for row in agents if row["agent_id"] == "startup-expert"), None
                            )
                            if (
                                expert
                                and expert["state"] == "running"
                                and not expert["startup_pending"]
                            ):
                                result["expert_ready_seconds"] = round(time.monotonic() - start, 3)
                                result["expert_within_16_seconds"] = (
                                    result["expert_ready_seconds"] <= 16
                                )
                                break
                            if expert and expert["state"] == "failed":
                                result["expert_failed"] = True
                                break
                        elif health["ready"]:
                            break
                    except (urllib.error.URLError, OSError, TimeoutError):
                        pass
                    time.sleep(0.1)
                if check_chat and token and "expert_ready_seconds" in result:
                    result.update(verify_chat(base, token, start))
                if token:
                    result["startup_checkpoints"] = request(base, "/api/health/startup", token)[
                        "checkpoints"
                    ]
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provider-db", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--existing-workspace", action="store_true")
    parser.add_argument(
        "--verify-chat", action="store_true", help="Send one short real model request per sample"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = [
        sample(
            args.runtime.resolve(),
            args.output.resolve(),
            number,
            args.provider_db,
            args.model,
            args.verify_chat,
            args.existing_workspace,
        )
        for number in range(1, args.samples + 1)
    ]
    (args.output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
