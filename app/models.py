"""Persistence models. Evaluation metadata never supplies working document fields."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
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
    source_extraction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extractions.id"))
    input_hash: Mapped[str | None]
    preserved_error: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
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


class OperationalConfig(Base):
    __tablename__ = "operational_config"
    __table_args__ = (CheckConstraint("id = 1"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ProcessingPipeline(Base):
    __tablename__ = "processing_pipelines"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    stage: Mapped[str] = mapped_column(default="text")
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    error: Mapped[str | None]
    result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("linking_results.id"))


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


class Embedding(Base):
    """Successful physical encodings, shared only by identical prepared content/config."""

    __tablename__ = "embeddings"
    __table_args__ = (
        CheckConstraint("tokens > 0 AND chunks > 0 AND duration_ms >= 0"),
        CheckConstraint("NOT truncated"),
        CheckConstraint("abs(vector_norm(vector) - 1) < 0.0001"),
        Index(
            "ix_embeddings_vector_hnsw",
            "vector",
            postgresql_using="hnsw",
            postgresql_ops={"vector": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    input_hash: Mapped[str] = mapped_column(unique=True)
    text_hash: Mapped[str]
    model: Mapped[str]
    revision: Mapped[str]
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    vector: Mapped[list[float]] = mapped_column(Vector(1024))
    tokens: Mapped[int]
    chunks: Mapped[int]
    truncated: Mapped[bool] = mapped_column(default=False)
    duration_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentEmbedding(Base):
    """Append-only provenance; identical texts in new corpus versions reuse a vector."""

    __tablename__ = "document_embeddings"
    __table_args__ = (UniqueConstraint("document_id", "embedding_id", "original_text_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    embedding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("embeddings.id"), index=True)
    original_text_hash: Mapped[str]
    original_chars: Mapped[int]
    prepared_chars: Mapped[int]
    cache_hit: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LinkingResult(Base):
    """Document outcome, including empty pools and errors; never a confirmed link."""

    __tablename__ = "linking_results"
    __table_args__ = (
        CheckConstraint("outcome IN ('recommendation','review','none','error')"),
        CheckConstraint("method IN ('rules','semantic','combined')"),
        Index(
            "uq_linking_success",
            "input_hash",
            unique=True,
            postgresql_where=text("outcome <> 'error'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_configs.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), unique=True)
    input_hash: Mapped[str] = mapped_column(index=True)
    method: Mapped[str]
    method_version: Mapped[str]
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    outcome: Mapped[str]
    reasons: Mapped[list[str]] = mapped_column(JSONB)
    recommendations: Mapped[list[str]] = mapped_column(JSONB)
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LinkSuggestion(Base):
    __tablename__ = "link_suggestions"
    __table_args__ = (
        UniqueConstraint("result_id", "target_id"),
        UniqueConstraint("result_id", "rank"),
        CheckConstraint("rank > 0"),
        CheckConstraint("decision IN ('recommendation','review','none')"),
        CheckConstraint("link_type IN ('изменение','основание','исполнение')"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("linking_results.id"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    link_type: Mapped[str]
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    penalties: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    score: Mapped[float]
    rank: Mapped[int]
    decision: Mapped[str]
    source_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    target_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)


class WorkingSet(Base):
    __tablename__ = "working_sets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    corpus_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpus_versions.id"))
    name: Mapped[str]
    idempotency_key: Mapped[uuid.UUID] = mapped_column(unique=True)
    request_hash: Mapped[str]
    baseline: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkingCopy(Base):
    __tablename__ = "working_copies"
    __table_args__ = (UniqueConstraint("working_set_id", "source_document_id"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    working_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("working_sets.id"), index=True)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    source_result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("linking_results.id"))
    source_result_current: Mapped[bool] = mapped_column(default=False)


class ReviewState(Base):
    """Mutable workflow state, separate from immutable algorithm outcomes."""

    __tablename__ = "review_states"
    __table_args__ = (
        CheckConstraint("status IN ('open','completed')"),
        CheckConstraint("version >= 0"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    version: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="open")
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    needs_review: Mapped[bool] = mapped_column(default=False)


class CardEdit(Base):
    __tablename__ = "card_edits"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    review_action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "review_actions.id",
            use_alter=True,
            name="fk_card_edit_action",
        )
    )
    before: Mapped[dict[str, Any]] = mapped_column(JSONB)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewSession(Base):
    __tablename__ = "review_sessions"
    __table_args__ = (CheckConstraint("duration_ms IS NULL OR duration_ms >= 0"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("linking_results.id"))
    context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None]


class ReviewAction(Base):
    __tablename__ = "review_actions"
    __table_args__ = (
        UniqueConstraint("document_id", "idempotency_key"),
        CheckConstraint(
            "action IN ('confirm','choose_other','reject','manual','complete','without_links',"
            "'reopen','remove','edit')"
        ),
        CheckConstraint("duration_ms >= 0"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_sessions.id"))
    result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("linking_results.id"))
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("link_suggestions.id"))
    chosen_target_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    action: Mapped[str]
    reason: Mapped[str | None]
    idempotency_key: Mapped[uuid.UUID]
    request_hash: Mapped[str]
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int]
    state_version: Mapped[int]


class Link(Base):
    __tablename__ = "links"
    __table_args__ = (
        CheckConstraint("origin IN ('confirmed','manual')"),
        CheckConstraint("link_type IN ('изменение','основание','исполнение')"),
        CheckConstraint("source_id <> target_id"),
        Index(
            "uq_link_active",
            "source_id",
            "target_id",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    link_type: Mapped[str]
    origin: Mapped[str]
    review_action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_actions.id"))
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("link_suggestions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removal_action_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_actions.id"))


class ExperimentSeries(Base):
    __tablename__ = "experiment_series"
    __table_args__ = (CheckConstraint("status IN ('queued','running','done','error')"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str]
    corpus_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpus_versions.id"))
    idempotency_key: Mapped[uuid.UUID] = mapped_column(unique=True)
    request_hash: Mapped[str]
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="queued")
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    selected_model_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_configs.id"))
    error: Mapped[str | None]
    dataset: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint("series_id", "variant"),
        CheckConstraint(
            "status IN ('queued','frozen','running','done','error',"
            "'NO_FEASIBLE_CONFIGURATION','BLOCKED')"
        ),
        CheckConstraint("progress >= 0 AND total >= progress"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_series.id"), index=True)
    variant: Mapped[str]
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_configs.id"))
    method: Mapped[str]
    ablation: Mapped[str] = mapped_column(default="none")
    status: Mapped[str] = mapped_column(default="queued")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    protocol_hash: Mapped[str | None]
    reused_test: Mapped[bool] = mapped_column(default=False)
    test_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[int] = mapped_column(default=0)
    total: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None]
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ExperimentMetric(Base):
    __tablename__ = "experiment_metrics"
    __table_args__ = (CheckConstraint("split IN ('tune','test')"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), primary_key=True)
    split: Mapped[str] = mapped_column(primary_key=True)
    slice: Mapped[str] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[float | None]
    numerator: Mapped[int | None]
    denominator: Mapped[int | None]


class ThresholdCurve(Base):
    __tablename__ = "threshold_curves"
    __table_args__ = (CheckConstraint("split = 'tune' AND purpose = 'tuning'"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), primary_key=True)
    index: Mapped[int] = mapped_column(primary_key=True)
    split: Mapped[str] = mapped_column(default="tune")
    purpose: Mapped[str] = mapped_column(default="tuning")
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ExperimentResult(Base):
    __tablename__ = "experiment_results"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("linking_results.id"))
    timing_id: Mapped[int] = mapped_column(ForeignKey("stage_timings.id"))
    reused: Mapped[bool]


class ExperimentArtifact(Base):
    """Many-to-many physical provenance; a shared call is never reassigned to a run."""

    __tablename__ = "experiment_artifacts"
    __table_args__ = (
        CheckConstraint("num_nonnulls(extraction_id,llm_call_id,embedding_id,timing_id) = 1"),
        *(
            UniqueConstraint("run_id", name)
            for name in ("extraction_id", "llm_call_id", "embedding_id", "timing_id")
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), index=True)
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extractions.id"))
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("llm_calls.id"))
    embedding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("embeddings.id"))
    timing_id: Mapped[int | None] = mapped_column(ForeignKey("stage_timings.id"))
