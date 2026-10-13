"""Administrator-owned DSH MCP declarations, translated to ACP at session startup."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator


class MCPExtension(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    transport: Literal["stdio", "http"] = "stdio"
    command: str = Field(
        default="", max_length=4096, description="Absolute local executable path for stdio MCP"
    )
    args: list[str] = Field(default_factory=list, max_length=100)
    env: dict[str, str] = Field(default_factory=dict, max_length=100)
    url: str = Field(default="", max_length=4096)
    headers: dict[str, str] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def validate_transport(self) -> MCPExtension:
        values = [
            self.name,
            self.command,
            *self.args,
            *self.env,
            *self.env.values(),
            self.url,
            *self.headers,
            *self.headers.values(),
        ]
        if any("\x00" in value or len(value) > 16_000 for value in values):
            raise ValueError("Invalid MCP declaration")
        if self.transport == "stdio":
            if not Path(self.command).is_absolute() or any(
                not key or "=" in key for key in self.env
            ):
                raise ValueError(
                    "An absolute command path and valid environment names are required"
                )
            if self.url or self.headers:
                raise ValueError("HTTP settings are not used by stdio")
        else:
            parsed = urlparse(self.url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError(
                    "An HTTP or HTTPS MCP URL without embedded credentials is required"
                )
            if self.command or self.args or self.env:
                raise ValueError("Process settings are not used by HTTP")
            names = [name.lower() for name in self.headers]
            if len(names) != len(set(names)) or any(
                not key.strip() or "\r" in key or "\n" in key or "\r" in value or "\n" in value
                for key, value in self.headers.items()
            ):
                raise ValueError("Invalid or duplicate HTTP headers")
        return self

    def acp(self) -> dict[str, Any]:
        if self.transport == "http":
            return {
                "name": self.name,
                "type": "http",
                "url": self.url,
                "headers": [{"name": name, "value": value} for name, value in self.headers.items()],
            }
        return {
            "name": self.name,
            "command": self.command,
            "args": self.args,
            "env": [{"name": name, "value": value} for name, value in self.env.items()],
        }


class MCPExtensions(BaseModel):
    extensions: list[MCPExtension] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def unique_names(self) -> MCPExtensions:
        names = [item.name.casefold() for item in self.extensions]
        if len(names) != len(set(names)):
            raise ValueError("MCP names must be unique")
        return self
