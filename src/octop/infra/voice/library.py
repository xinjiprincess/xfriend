"""User-owned voice references, stored with the rest of the backed-up application data."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import shutil
import time
import uuid
import wave
from dataclasses import replace
from pathlib import Path
from typing import Any

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.voice import adapters
from octop.infra.voice.manager import ResolvedVoiceProvider, VoiceManager
from octop.infra.voice.preferences import load_preferences

MAX_SAMPLE_BYTES = 7 * 1024 * 1024


class VoiceLibrary:
    def __init__(self, home: Path, manager: VoiceManager) -> None:
        self.root = home / "voice-library"
        self.manager = manager

    def _folder(self, user_id: int, voice_id: str) -> Path:
        try:
            if str(uuid.UUID(voice_id)) != voice_id:
                raise ValueError
        except ValueError as exc:
            raise OctopError(ErrorCode.NOT_FOUND, "Voice reference not found") from exc
        return self.root / str(user_id) / voice_id

    def get(self, user_id: int, voice_id: str) -> dict[str, Any]:
        try:
            return dict(
                json.loads((self._folder(user_id, voice_id) / "voice.json").read_text("utf-8"))
            )
        except FileNotFoundError as exc:
            raise OctopError(ErrorCode.NOT_FOUND, "Voice reference not found") from exc

    def list(self, user_id: int) -> list[dict[str, Any]]:
        return [
            self.get(user_id, path.parent.name)
            for path in sorted((self.root / str(user_id)).glob("*/voice.json"))
        ]

    @staticmethod
    def _audio_type(data: bytes) -> str:
        if not data or len(data) > MAX_SAMPLE_BYTES:
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Invalid voice reference")
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            try:
                with wave.open(io.BytesIO(data)) as audio:
                    if audio.getnframes() <= 0 or audio.getnchannels() not in (1, 2):
                        raise ValueError
                    expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                    if len(audio.readframes(audio.getnframes())) != expected:
                        raise ValueError
            except (wave.Error, EOFError, ValueError) as exc:
                raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Invalid voice reference") from exc
            return "audio/wav"
        # Accept MPEG audio references without trusting the uploaded filename or MIME.
        if data.startswith(b"ID3") or (
            len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
        ):
            return "audio/mpeg"
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Invalid voice reference")

    def create(
        self, user_id: int, name: str, data: bytes, *, description: str = ""
    ) -> dict[str, Any]:
        if not name.strip():
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Voice name is empty")
        mime = self._audio_type(data)
        voice_id = str(uuid.uuid4())
        record = {
            "id": voice_id,
            "name": name,
            "description": description,
            "mime": mime,
            "created_at": int(time.time()),
            "mode": "design" if description else "reference",
        }
        folder = self._folder(user_id, voice_id)
        folder.mkdir(parents=True)
        try:
            (folder / "sample").write_bytes(data)
            (folder / "voice.json").write_text(json.dumps(record, ensure_ascii=False), "utf-8")
        except BaseException:
            shutil.rmtree(folder)
            raise
        return record

    def rename(self, user_id: int, voice_id: str, name: str) -> dict[str, Any]:
        record = {**self.get(user_id, voice_id), "name": name}
        folder = self._folder(user_id, voice_id)
        # Unique temporary filenames also permit concurrent rename requests.
        temporary = folder / f"{uuid.uuid4()}.tmp"
        temporary.write_text(json.dumps(record, ensure_ascii=False), "utf-8")
        temporary.replace(folder / "voice.json")
        return record

    def delete(self, user_id: int, voice_id: str) -> None:
        self.get(user_id, voice_id)
        shutil.rmtree(self._folder(user_id, voice_id))
        preferences = load_preferences(self.manager, user_id)
        if preferences.voice_id == f"library:{voice_id}":
            # Clearing a stale reference must still work if its provider was disabled.
            self.manager.settings.set(
                f"assistant_voice:{user_id}",
                json.dumps(
                    {
                        **preferences.__dict__,
                        "voice_id": "",
                    }
                ),
            )

    def validate_selection(self, user_id: int, voice_id: str, provider: str) -> None:
        self.get(user_id, voice_id.removeprefix("library:"))
        if self.manager.resolve(provider or self.manager.get_active()["tts"]).kind != "mimo":
            raise OctopError(
                ErrorCode.VOICE_CAPABILITY_MISMATCH,
                "Voice library requires a MiMo synthesis provider",
            )

    def sample(self, user_id: int, voice_id: str) -> tuple[bytes, str]:
        record = self.get(user_id, voice_id)
        return (self._folder(user_id, voice_id) / "sample").read_bytes(), record["mime"]

    async def synthesize(
        self, user_id: int, voice_id: str, text: str, provider: str | None
    ) -> bytes:
        sample, mime = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self.sample(user_id, voice_id)
        )
        return await self._generate(
            text,
            provider,
            "mimo-v2.5-tts-voiceclone",
            voice_sample=f"data:{mime};base64,{base64.b64encode(sample).decode('ascii')}",
        )

    async def design(self, text: str, provider: str, description: str) -> bytes:
        return await self._generate(
            text, provider, "mimo-v2.5-tts-voicedesign", voice_description=description
        )

    async def _generate(self, text: str, provider: str | None, model: str, **extra: str) -> bytes:
        def resolve() -> ResolvedVoiceProvider:
            name = provider or self.manager.get_active()["tts"]
            self.manager.validate_provider_name(name, capability="tts")
            return self.manager.resolve(name)

        resolved = await asyncio.get_running_loop().run_in_executor(None, resolve)
        if resolved.kind != "mimo" or resolved.row is None:
            raise OctopError(
                ErrorCode.VOICE_CAPABILITY_MISMATCH,
                "Voice library requires a MiMo synthesis provider",
            )
        row = replace(
            resolved.row,
            extra_json=json.dumps(
                {
                    **resolved.row.get_extra(),
                    "tts_model": model,
                    **extra,
                }
            ),
        )
        output = bytearray()
        async with asyncio.timeout(120):
            async for part in adapters.synthesize_mimo(row, text, voice_id=None, speed=1.0):
                output.extend(part)
                if len(output) > MAX_SAMPLE_BYTES:
                    raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Invalid voice reference")
        # Streaming WAV uses unknown-length sentinels. Persist a conventional WAV.
        if len(output) <= 44:
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Invalid voice reference")
        output[4:8] = (len(output) - 8).to_bytes(4, "little")
        output[40:44] = (len(output) - 44).to_bytes(4, "little")
        self._audio_type(bytes(output))
        return bytes(output)
