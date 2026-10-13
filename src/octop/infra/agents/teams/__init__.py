"""Expert-team hosts and member roster."""

from typing import TYPE_CHECKING, Any

from octop.infra.agents.teams.jobs import TeamJobTracker
from octop.infra.agents.teams.service import (
    HOST_TOOLS_ALLOWED,
    HOST_TOOLS_DISABLED,
    TEAM_AVATAR_URL,
    TEAM_KIND,
    TEAM_MIN_MEMBERS,
    TEAM_TEMPLATE_NAME,
    TEMPLATE_DIR,
    TeamService,
    agent_kind,
    host_tools_disabled,
    is_team_agent,
    team_icon_url,
)

if TYPE_CHECKING:
    from octop.infra.agents.teams.team_manager import (
        TeamManager,
        host_system_prompt,
        wire_host_dispatch,
    )


def __getattr__(name: str) -> Any:
    if name in {"TeamManager", "host_system_prompt", "wire_host_dispatch"}:
        from octop.infra.agents.teams import team_manager

        return getattr(team_manager, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "HOST_TOOLS_ALLOWED",
    "HOST_TOOLS_DISABLED",
    "TEAM_AVATAR_URL",
    "TEAM_KIND",
    "TEAM_MIN_MEMBERS",
    "TEAM_TEMPLATE_NAME",
    "TEMPLATE_DIR",
    "TeamJobTracker",
    "TeamManager",
    "TeamService",
    "agent_kind",
    "host_system_prompt",
    "host_tools_disabled",
    "is_team_agent",
    "team_icon_url",
    "wire_host_dispatch",
]
