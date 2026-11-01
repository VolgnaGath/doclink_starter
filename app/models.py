"""Persistence models. Evaluation metadata never supplies working document fields."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("namespace", "external_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str]
    inn: Mapped[str | None]
    namespace: Mapped[str] = mapped_column(default="upload")
    external_key: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Counterparty(Base):
    __tablename__ = "counterparties"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str]
    inn: Mapped[str | None]
    kpp: Mapped[str | None]
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list)


class CorpusVersion(Base):
    __tablename__ = "corpus_versions"
    __table_args__ = (CheckConstraint("tune_share >= 0 AND tune_share <= 1"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str]
    fingerprint: Mapped[str] = mapped_column(unique=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    seed: Mapped[int]
    tune_share: Mapped[float] = mapped_column(Numeric(8, 7))
    notes: Mapped[str | None]
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("corpus_version_id", "corpus_doc_id"),
        CheckConstraint("source IN ('upload', 'mock1c', 'corpus')"),
        CheckConstraint("split IS NULL OR split IN ('tune', 'test')"),
        CheckConstraint("status IN ('new','text','extracted','linked','review','manual','error')"),
        CheckConstraint(
            "doc_kind IS NULL OR doc_kind IN ('договор','допсоглашение','счёт','акт','УПД')"
        ),
        Index("ix_documents_client_registered", "client_id", "registered_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"))
    corpus_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("corpus_versions.id"))
    corpus_doc_id: Mapped[str | None]
    deal_id: Mapped[str | None]
    split: Mapped[str | None]
    source: Mapped[str]
    filename: Mapped[str]
    sha256: Mapped[str]
    mime: Mapped[str]
    storage_path: Mapped[str]
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_anonymized: Mapped[bool] = mapped_column(default=False)
    in_scope: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(default="new", index=True)
    gold: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    doc_kind: Mapped[str | None]
    number: Mapped[str | None]
    date: Mapped[date | None]
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("counterparties.id"))
    card_model_config_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_configs.id"))
    card_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extractions.id", use_alter=True, name="fk_document_card_extraction")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentText(Base):
    __tablename__ = "document_texts"
    __table_args__ = (
        CheckConstraint("pages >= 0 AND chars >= 0 AND ocr_pages BETWEEN 0 AND pages"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    text: Mapped[str]
    pages: Mapped[int]
    chars: Mapped[int]
    ocr_pages: Mapped[int]
    anonymized_text: Mapped[str | None]


class AnonMap(Base):
    __tablename__ = "anon_maps"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    kind: Mapped[str]
    original: Mapped[str]
    replacement: Mapped[str]


class StageTiming(Base):
    __tablename__ = "stage_timings"
    __table_args__ = (CheckConstraint("duration_ms >= 0"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    stage: Mapped[str]
    duration_ms: Mapped[int]
    ok: Mapped[bool]
    error: Mapped[str | None]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelConfig(Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        CheckConstraint("provider IN ('yandex','gigachat','ollama','regex')"),
        CheckConstraint("placement IN ('cloud','open-weights-cloud','local')"),
        CheckConstraint("price_in_1k >= 0 AND price_out_1k >= 0"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(unique=True)
    provider: Mapped[str]
    model_uri: Mapped[str]
    placement: Mapped[str]
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    price_in_1k: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    price_out_1k: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    v_prefill: Mapped[float | None]
    v_decode: Mapped[float | None]
    active: Mapped[bool] = mapped_column(default=False)
    default_card: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Prompt(Base):
    __tablename__ = "prompts"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str]
    version: Mapped[str]
    text: Mapped[str]
    schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint("status IN ('started','success','invalid','error','uncertain')"),
        CheckConstraint("cost_rub >= 0 AND reserved_rub >= 0"),
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0"),
        Index(
            "uq_llm_success",
            "input_hash",
            "model_config_id",
            "prompt_id",
            unique=True,
            postgresql_where=text("status = 'success'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_config_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_configs.id"))
    prompt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prompts.id"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    purpose: Mapped[str] = mapped_column(default="extract")
    input_hash: Mapped[str]
    root_input_hash: Mapped[str] = mapped_column(index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    latency_ms: Mapped[int | None]
    cost_rub: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    reserved_rub: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    paid: Mapped[bool]
    status: Mapped[str] = mapped_column(default="started", index=True)
    error: Mapped[str | None]
    response_raw: Mapped[str | None]
    truncated: Mapped[bool]
    reconciled: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Extraction(Base):
    __tablename__ = "extractions"
    __table_args__ = (
        Index("ix_extractions_document_model", "document_id", "model_config_id"),
        Index(
            "uq_extraction_current",
            "document_id",
            "model_config_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    model_config_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_configs.id"))
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("llm_calls.id"))
    doc_kind: Mapped[str | None]
    number: Mapped[str | None]
    date: Mapped[date | None]
    cp_name: Mapped[str | None]
    cp_inn: Mapped[str | None]
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    subject: Mapped[str | None]
    contract_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    supp_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    invoice_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence_checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    valid: Mapped[bool]
    evidence_ok: Mapped[bool]
    cache_hit: Mapped[bool] = mapped_column(default=False)
    is_current: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','done','error')"),
        Index(
            "uq_job_active",
            "type",
            "document_id",
            "model_config_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    type: Mapped[str]
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_configs.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(default="queued", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None]


class AnonIdentity(Base):
    """Client-scoped dictionary; anon_maps retain document-specific reverse mappings."""

    __tablename__ = "anon_identities"
    __table_args__ = (
        UniqueConstraint("client_id", "kind", "original"),
        UniqueConstraint("client_id", "replacement"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id"))
    kind: Mapped[str]
    original: Mapped[str]
    replacement: Mapped[str]
