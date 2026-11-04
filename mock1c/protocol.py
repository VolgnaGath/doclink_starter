"""Small, explicit wire protocol; no application/database dependencies."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class InboxDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    client_id: UUID
    client_name: str = Field(min_length=1, max_length=300)
    filename: str = Field(min_length=1, max_length=255)
    registered_at: AwareDatetime
    is_anonymized: bool = False
    sha256: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("Only a filename is allowed")
        if not value.lower().endswith((".pdf", ".docx", ".txt")):
            raise ValueError("Unsupported file type")
        return value


class LinkEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    link_id: UUID
    review_action_id: UUID
    operation: Literal["create", "remove"]
    source_id: UUID
    target_id: UUID
    link_type: Literal["изменение", "основание", "исполнение"]
    occurred_at: AwareDatetime


def utc_date(value: datetime) -> str:
    from datetime import UTC

    return value.astimezone(UTC).isoformat()
