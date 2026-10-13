"""Check the packaged Mac login endpoint without touching the operator's account."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="xfriend-mac-smoke-") as directory:
        home = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        (home / "desktop-settings.json").write_text(json.dumps({"port": port, "locale": "zh"}))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OCTOP_", "XFRIEND_"))}
        env["XFRIEND_HOME"] = str(home)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        started = time.monotonic()
        process = subprocess.Popen(
            [str(args.app / "Contents/MacOS/Xfriend")], env=env, start_new_session=True
        )
        try:
            while time.monotonic() - started < 45:
                if process.poll() is not None:
                    raise RuntimeError("Packaged desktop exited before login")
                try:
                    with opener.open(f"http://127.0.0.1:{port}/login", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise TimeoutError("Packaged login did not become available")
            seconds = round(time.monotonic() - started, 3)
            try:
                opener.open(f"http://127.0.0.1:{port}/api/desktop/mobile/status", timeout=5)
            except urllib.error.HTTPError as exc:
                if exc.code != 401:
                    raise
            else:
                raise AssertionError("Anonymous mobile management accepted")
            if (home / "accounts").exists():
                raise AssertionError("Account data created before login")
            print(json.dumps({"login_http_seconds": seconds, "anonymous_mobile_status": 401}))
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
