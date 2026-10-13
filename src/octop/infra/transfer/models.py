"""Versioned portable objects. Never deserialize database rows or runtime commands."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRef(StrictModel):
    provider_ref: str
    model_id: str = Field(min_length=1, max_length=300)


class Provider(StrictModel):
    ref: str
    name: str = Field(min_length=1, max_length=100)
    family: Literal["text", "voice"] = "text"
    kind: str = Field(min_length=1, max_length=80)
    capability: str = "both"
    base_url: str | None = Field(default=None, max_length=2000)
    models: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=1000)
    extra: dict[str, JsonValue] = Field(default_factory=dict)
    has_secret: bool = False

    @field_validator("base_url")
    @classmethod
    def url_without_credentials(cls, value: str | None) -> str | None:
        from urllib.parse import urlsplit

        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise ValueError("invalid provider URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("provider URL must not contain credentials or query parameters")
        return value


class RuntimePreferences(StrictModel):
    max_iters: int | None = Field(default=None, ge=1, le=1000)
    max_input_length: int | None = Field(default=None, ge=1, le=10000000)
    max_tokens: int | None = Field(default=None, ge=1, le=10000000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    tools_disabled: list[str] = Field(default_factory=list, max_length=500)


class Expert(StrictModel):
    ref: str
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["expert", "team"] = "expert"
    description: str | None = Field(default=None, max_length=20000)
    system_prompt: str | None = Field(default=None, max_length=200000)
    welcome_message: str | None = Field(default=None, max_length=20000)
    persona_mbti: str | None = Field(default=None, max_length=4)
    color: str | None = Field(default=None, max_length=50)
    icon_name: str | None = Field(default=None, max_length=80)
    bundled_icon: str | None = None
    avatar_file: str | None = None
    model: ModelRef | None = None
    preferences: RuntimePreferences = Field(default_factory=RuntimePreferences)
    engine: Literal["native", "dsh"] = "native"
    member_refs: list[str] = Field(default_factory=list, max_length=500)
    member_models: dict[str, ModelRef] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("bundled_icon")
    @classmethod
    def bundled_only(cls, value: str | None) -> str | None:
        if value and not re.fullmatch(r"/experts/avatars/[a-zA-Z0-9_-]+\.svg", value):
            raise ValueError("only bundled portrait paths are portable")
        return value


class Package(StrictModel):
    format: Literal["xfriend-portable"] = "xfriend-portable"
    format_version: Literal[1, 2] = 2
    package_id: str
    client_version: str
    providers: list[Provider] = Field(default_factory=list, max_length=1000)
    experts: list[Expert] = Field(default_factory=list, max_length=2000)
    checksums: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self) -> Self:
        provider_refs = {p.ref for p in self.providers}
        expert_refs = {e.ref for e in self.experts}
        if len(provider_refs) != len(self.providers) or len(expert_refs) != len(self.experts):
            raise ValueError("duplicate resource reference")
        if provider_refs & expert_refs:
            raise ValueError("resource references overlap")
        for ref in provider_refs | expert_refs:
            if not re.fullmatch(r"[a-z]+:[a-zA-Z0-9_-]{1,100}", ref):
                raise ValueError("invalid resource reference")
        providers = {p.ref: p for p in self.providers}
        experts = {e.ref: e for e in self.experts}
        for expert in self.experts:
            if self.format_version == 1 and expert.member_models:
                raise ValueError("team model assignments require format version 2")
            if set(expert.member_models) - set(expert.member_refs):
                raise ValueError("model assigned to non-member")
            for model in expert.member_models.values():
                provider = providers.get(model.provider_ref)
                if provider is None or provider.family != "text":
                    raise ValueError("missing team member model provider")
            if expert.model:
                p = providers.get(expert.model.provider_ref)
                if p is None or p.family != "text":
                    raise ValueError("missing text provider reference")
            if expert.kind == "team":
                if len(set(expert.member_refs)) != len(expert.member_refs):
                    raise ValueError("duplicate team member")
                if len(expert.member_refs) < 2:
                    raise ValueError("a team requires two members")
                for ref in expert.member_refs:
                    if ref not in experts or experts[ref].kind != "expert":
                        raise ValueError("missing expert member reference")
            elif expert.member_refs:
                raise ValueError("an expert cannot have team members")
        return self


class ExportSelection(StrictModel):
    agent_ids: list[str] = Field(default_factory=list, max_length=2000)
    provider_ids: list[int] = Field(default_factory=list, max_length=1000)
    voice_provider_ids: list[int] = Field(default_factory=list, max_length=1000)
    include_skills: bool = True
    include_secrets: bool = False
    password: str = Field(default="", max_length=1024, repr=False)


class Choice(StrictModel):
    action: Literal["create", "reuse", "skip"] = "create"
    target: str | None = None


class ImportPlan(StrictModel):
    choices: dict[str, Choice] = Field(default_factory=dict)
    trust_skills: bool = False


class CommitRequest(StrictModel):
    revision: str
    password: str = Field(default="", max_length=1024, repr=False)
