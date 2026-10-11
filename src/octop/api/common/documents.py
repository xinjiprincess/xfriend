"""Shared HTTP schema for conversation document selection."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class DocumentSelection(BaseModel):
    format: Literal["html", "pdf", "docx", "png"]
    mode: Literal["all", "turn", "selected", "range"] = "all"
    indices: list[int] = Field(default_factory=list, max_length=100000)
    start: int = Field(default=0, ge=0)
    end: int = Field(default=0, ge=0)
    digest: str = Field(description="Preview digest; rejects stale selections")


class DocumentPreview(BaseModel):
    digest: str
    messages: list[dict[str, Any]]
