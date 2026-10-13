"""Team-scoped model choices and optimistic configuration revisions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from octop.infra.db.repos.agents import AgentRow


def team_config(row: AgentRow) -> dict[str, Any]:
    try:
        value = json.loads(row.config_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def member_models(row: AgentRow | None) -> dict[str, str]:
    if row is None:
        return {}
    value = team_config(row).get("team_member_models", {})
    if not isinstance(value, dict):
        return {}
    return {key: model for key, model in value.items() if isinstance(model, str) and model}


def configuration_revision(row: AgentRow, roster: list[str]) -> str:
    payload = {
        "name": row.name,
        "description": row.description,
        "model": row.default_model,
        "prompt": row.system_prompt,
        "welcome": row.welcome_message,
        "config": team_config(row),
        "members": roster,
        "color": row.color,
        "icon": row.icon_name,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
