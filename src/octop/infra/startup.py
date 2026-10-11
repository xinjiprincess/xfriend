"""Monotonic, credential-free startup checkpoints shared by logs and health."""

from __future__ import annotations

import logging
import time
import uuid
from typing import TypedDict

logger = logging.getLogger(__name__)


class StartupSnapshot(TypedDict):
    startup_id: str
    phase: str
    ready: bool
    elapsed_ms: int
    checkpoints: dict[str, int]
    failure: str | None


class StartupProgress:
    def __init__(self) -> None:
        self.startup_id = str(uuid.uuid4())
        self.started = time.monotonic()
        self.phase = "created"
        self.ready = False
        self.checkpoints: dict[str, int] = {}
        self.failure: str | None = None

    def mark(self, phase: str) -> None:
        self.phase = phase
        elapsed = int((time.monotonic() - self.started) * 1000)
        self.checkpoints[phase] = elapsed
        logger.info("startup id=%s phase=%s elapsed_ms=%s", self.startup_id, phase, elapsed)

    def snapshot(self) -> StartupSnapshot:
        return {
            "startup_id": self.startup_id,
            "phase": self.phase,
            "ready": self.ready,
            "elapsed_ms": self.checkpoints.get(
                "ready", int((time.monotonic() - self.started) * 1000)
            ),
            "checkpoints": dict(self.checkpoints),
            "failure": self.failure,
        }
