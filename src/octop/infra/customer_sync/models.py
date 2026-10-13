"""The v2 sync wire contract; payload fields retain Android's JSON spelling."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

EntityType = Literal[
    "customer",
    "communication",
    "opportunity",
    "followup",
    "expert",
    "team",
    "skill",
    "knowledge",
    "model_profile",
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Entity(Model):
    entity_type: EntityType
    entity_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(default=0, ge=0)
    deleted: bool = False
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def finite_bounded_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if (
            len(
                json.dumps(
                    value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode()
            )
            > 2 * 1024 * 1024
        ):
            raise ValueError("sync payload exceeds 2 MiB")
        return value


class Operation(Model):
    operation_id: str
    entity_type: EntityType
    entity_id: str
    base_revision: int = Field(ge=0)
    deleted: bool = False
    payload: dict[str, JsonValue]


class Result(Model):
    operation_id: str
    status: Literal["applied", "duplicate", "conflict"]
    revision: int = Field(ge=0)
    server_entity: Entity | None = None


class PushResponse(Model):
    results: list[Result] = Field(max_length=100)
    cursor: int = Field(ge=0)


class PullResponse(Model):
    changes: list[Entity] = Field(max_length=100)
    cursor: int = Field(ge=0)
    has_more: bool


class Edit(Entity):
    source: Literal["manual", "ai"] = "manual"
    confirmed: bool = False
    sync_secret: bool = False


class Resolution(Model):
    operation_id: str
    choice: Literal["local", "server"]


class Journal(Model):
    cursor: int = 0
    entities: list[Entity] = Field(default_factory=list)
    pending: list[Operation] = Field(default_factory=list)
    conflicts: dict[str, Entity] = Field(default_factory=dict)
    remote: dict[str, Entity] = Field(default_factory=dict)
