"""Per-user assistant voice choices; service credentials remain administrator-owned."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from octop.infra.voice.manager import VoiceManager


@dataclass(frozen=True)
class AssistantVoicePreferences:
    stt: str = ""
    tts: str = ""
    voice_id: str = ""
    auto_read: bool = False
    auto_send: bool = False


def load_preferences(manager: VoiceManager, user_id: int) -> AssistantVoicePreferences:
    raw = manager.settings.get(f"assistant_voice:{user_id}")
    return AssistantVoicePreferences(**json.loads(raw)) if raw else AssistantVoicePreferences()


def save_preferences(
    manager: VoiceManager, user_id: int, preferences: AssistantVoicePreferences
) -> AssistantVoicePreferences:
    for capability in ("stt", "tts"):
        name = getattr(preferences, capability)
        if name:
            manager.validate_provider_name(name, capability=capability)
    manager.settings.set(f"assistant_voice:{user_id}", json.dumps(asdict(preferences)))
    return preferences
