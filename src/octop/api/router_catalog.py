"""Declarative router imports: keep optional features out of desktop core boot."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouterSpec:
    module: str
    attribute: str
    prefix: str
    tags: tuple[str, ...]


ROUTERS = (
    RouterSpec("octop.api.routers.customer_sync", "router", "/api", ("settings",)),
    RouterSpec("octop.api.routers.setup", "router", "/api", ("setup",)),
    RouterSpec("octop.api.routers.licensing", "router", "/api", ("licensing",)),
    RouterSpec("octop.api.routers.auth", "router", "/api/auth", ("auth",)),
    RouterSpec("octop.api.routers.auth_oidc", "router", "/api/auth", ("auth",)),
    RouterSpec("octop.api.routers.auth_oauth", "router", "/api/auth", ("auth",)),
    RouterSpec("octop.api.routers.auth_ldap", "router", "/api/auth", ("auth",)),
    RouterSpec("octop.api.routers.invites", "public_router", "/api/auth/invite", ("auth",)),
    RouterSpec("octop.api.routers.preferences", "router", "/api", ("auth",)),
    RouterSpec("octop.api.routers.i18n", "router", "/api", ("i18n",)),
    RouterSpec("octop.api.routers.health", "router", "/api/health", ("health",)),
    RouterSpec("octop.api.routers.invites", "admin_router", "/api/users/invites", ("users",)),
    RouterSpec("octop.api.routers.user_roles", "router", "/api/users/roles", ("users",)),
    RouterSpec("octop.api.routers.users", "router", "/api/users", ("users",)),
    RouterSpec("octop.api.routers.agents", "router", "/api/agents", ("agents",)),
    RouterSpec("octop.api.routers.agent_tools", "router", "/api", ("agents",)),
    RouterSpec("octop.api.routers.acp", "router", "/api", ("agents",)),
    RouterSpec("octop.api.routers.engines", "router", "/api", ("agents",)),
    RouterSpec("octop.api.routers.execution", "router", "/api", ("agents",)),
    RouterSpec("octop.api.routers.platform_collaboration", "router", "/api", ("connectors",)),
    RouterSpec("octop.api.routers.chat", "router", "/api", ("chat",)),
    RouterSpec("octop.api.routers.slash", "router", "/api", ("slash",)),
    RouterSpec("octop.api.routers.connectors", "router", "/api", ("connectors",)),
    RouterSpec("octop.api.routers.bridge", "router", "/api", ("bridge",)),
    RouterSpec("octop.api.routers.knowledge_bases", "router", "/api", ("knowledge",)),
    RouterSpec("octop.api.routers.internal_mcp", "router", "/api", ("internal-mcp",)),
    RouterSpec("octop.api.routers.channels", "router", "/api", ("channels",)),
    RouterSpec("octop.api.routers.cron", "router", "/api", ("cron",)),
    RouterSpec("octop.api.routers.settings", "router", "/api", ("settings",)),
    RouterSpec("octop.api.routers.envs", "router", "/api", ("envs",)),
    RouterSpec("octop.api.routers.search", "router", "/api", ("search",)),
    RouterSpec("octop.api.routers.providers", "router", "/api/providers", ("providers",)),
    RouterSpec("octop.api.routers.voice", "router", "/api/voice", ("voice",)),
    RouterSpec("octop.api.routers.admin", "router", "/api/admin", ("admin",)),
    RouterSpec("octop.api.routers.backup", "router", "/api/admin", ("admin",)),
    RouterSpec("octop.api.routers.providers", "admin_router", "/api/admin/providers", ("admin",)),
    RouterSpec("octop.api.routers.voice", "admin_router", "/api/admin/voice/providers", ("admin",)),
    RouterSpec(
        "octop.api.routers.observability", "router", "/api/admin/observability", ("observability",)
    ),
    RouterSpec(
        "octop.api.routers.media_generation",
        "router",
        "/api/admin/media-generation",
        ("providers",),
    ),
    RouterSpec("octop.api.routers.tls", "router", "/api/admin/tls", ("tls",)),
    RouterSpec("octop.api.routers.security", "router", "/api/admin/security", ("security",)),
    RouterSpec(
        "octop.api.routers.storage_backends",
        "admin_router",
        "/api/admin/storage-backends",
        ("admin",),
    ),
    RouterSpec(
        "octop.api.routers.storage_backends",
        "user_router",
        "/api/storage-backends",
        ("storage-backends",),
    ),
    RouterSpec("octop.api.routers.filesystem", "router", "/api/filesystem", ("filesystem",)),
    RouterSpec("octop.api.routers.mbti", "router", "/api", ("mbti",)),
    RouterSpec("octop.api.routers.experts", "router", "/api", ("experts",)),
    RouterSpec("octop.api.routers.teams", "router", "/api", ("teams",)),
    RouterSpec("octop.api.routers.workspace", "router", "/api", ("workspace",)),
    RouterSpec("octop.api.routers.agent_files", "router", "/api", ("agent_files",)),
    RouterSpec("octop.api.routers.memory", "router", "/api", ("memory",)),
    RouterSpec("octop.api.routers.memory_portable", "router", "/api", ("memory",)),
    RouterSpec("octop.api.routers.transfer", "router", "/api", ("settings",)),
    RouterSpec("octop.api.routers.proactive_care", "router", "/api", ("proactive-care",)),
    RouterSpec("octop.api.routers.usage", "router", "/api", ("usage",)),
    RouterSpec("octop.api.routers.usage", "admin_router", "/api/admin", ("admin",)),
    RouterSpec("octop.api.routers.skill_packages", "router", "/api", ("skill-packages",)),
    RouterSpec("octop.api.routers.skills", "router", "/api", ("skills",)),
    RouterSpec("octop.api.routers.subagents", "router", "/api", ("subagents",)),
    RouterSpec("octop.api.routers.terminal", "router", "/api", ("terminal",)),
    RouterSpec("octop.api.routers.uploads", "router", "/api", ("chat",)),
    RouterSpec("octop.api.routers.update", "router", "/api", ("update",)),
    RouterSpec("octop.api.routers.browser", "router", "/api", ("browser",)),
    RouterSpec("octop.api.routers.desktop", "router", "/api", ("desktop",)),
    RouterSpec("octop.api.routers.ollama_models", "router", "/api", ("ollama",)),
    RouterSpec("octop.api.routers.onnx_models", "router", "/api", ("onnx",)),
    RouterSpec("octop.api.routers.plugins", "router", "/api", ("plugins",)),
)

MOBILE_ROUTER = RouterSpec("octop.api.routers.mobile", "router", "/api", ("mobile",))

CORE_MODULES = frozenset(
    {
        "octop.api.routers.agents",
        "octop.api.routers.auth",
        "octop.api.routers.auth_ldap",
        "octop.api.routers.auth_oauth",
        "octop.api.routers.auth_oidc",
        "octop.api.routers.chat",
        "octop.api.routers.engines",
        "octop.api.routers.execution",
        "octop.api.routers.experts",
        "octop.api.routers.health",
        "octop.api.routers.i18n",
        "octop.api.routers.invites",
        "octop.api.routers.licensing",
        "octop.api.routers.plugins",
        "octop.api.routers.preferences",
        "octop.api.routers.providers",
        "octop.api.routers.settings",
        "octop.api.routers.setup",
    }
)
