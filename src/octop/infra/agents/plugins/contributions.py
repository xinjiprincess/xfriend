"""Instantiate accepted expert contributions without modifying existing memories."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octop.infra.errors import ErrorCode, OctopError

if TYPE_CHECKING:
    from octop.infra.agents.manager import AgentManager
    from octop.infra.agents.plugins.manager import PluginManager
    from octop.infra.engines.execution import ExecutionService
    from octop.infra.users.identity import User


async def instantiate_expert(
    plugins: PluginManager,
    agents: AgentManager,
    execution: ExecutionService,
    user: User,
    plugin_id: str,
    template_id: str,
    name: str,
    workspace: str,
) -> dict[str, Any]:
    from octop.infra.agents.manager import AgentCreateSpec

    contributions = await asyncio.get_running_loop().run_in_executor(None, plugins.contributions)
    template = next(
        (
            item
            for item in contributions
            if item["plugin_id"] == plugin_id
            and item["id"] == template_id
            and item["type"] == "expert"
        ),
        None,
    )
    if template is None:
        raise OctopError(ErrorCode.NOT_FOUND, "Expert template not found")
    engine = template["engine"]
    if engine == "dsh":
        directory = Path(workspace).expanduser()
        if not user.is_admin:
            raise OctopError(ErrorCode.FORBIDDEN, "DSH requires administrator access")
        if (
            not directory.is_absolute()
            or not await asyncio.get_running_loop().run_in_executor(None, directory.is_dir)
            or str(directory).startswith(("\\\\", "//"))
        ):
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Choose an absolute local workspace")
    row = await agents.create(
        AgentCreateSpec(
            name=name,
            user_id=user.id,
            description=template["description"],
            system_prompt=template["system_prompt"],
            default_model=template["default_model"],
            config={
                "host_template": {
                    "plugin_id": plugin_id,
                    "template_id": template_id,
                    "version": template["plugin_version"],
                }
            },
        )
    )
    return await execution.bind(user, row.agent_id, engine, workspace)
