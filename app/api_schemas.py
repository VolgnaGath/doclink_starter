"""Public allowlists. ORM objects, raw provenance and storage paths are never serialized."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.linking.types import Fields, LinkingConfig, Penalty

Kind = Literal["договор", "допсоглашение", "счёт", "акт", "УПД", "другое"]
Status = Literal["new", "text", "extracted", "linked", "review", "manual", "error"]
Outcome = Literal["recommendation", "review", "none", "error"]


class Public(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](Public):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorBody(Public):
    code: str
    message: str
    details: list[dict[str, Any]] = Field(default_factory=list)


class ClientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=300)
    inn: str | None = Field(default=None, pattern=r"^(\d{10}|\d{12})$")


class ClientView(Public):
    id: UUID
    name: str
    inn: str | None
    namespace: str
    created_at: datetime


class CopyOrigin(Public):
    working_set_id: UUID
    source_document_id: UUID
    source_result_id: UUID | None
    initial_inputs: bool


class DocumentSummary(Public):
    id: UUID
    client_id: UUID
    corpus_version_id: UUID | None
    split: Literal["tune", "test"] | None
    source: Literal["upload", "mock1c", "corpus"]
    kind: Kind | None
    number: str | None
    date: date | None
    filename: str
    sha256: str
    mime: str
    card_model_config_id: UUID | None
    card_extraction_id: UUID | None
    is_anonymized: bool
    status: Status
    registered_at: datetime
    created_at: datetime


class TextView(Public):
    text: str
    pages: int
    chars: int
    ocr_pages: int


class ReferenceView(Public):
    number: str | None = None
    date: str | None = None
    quote: str | None = None


class EvidenceCheck(Public):
    field: str
    ok: bool
    quote: str | None = None
    original_quote: str | None = None
    method: str | None = None
    start: int | None = None
    end: int | None = None


class ExtractionView(Public):
    id: UUID
    document_id: UUID
    model_config_id: UUID
    llm_call_id: UUID | None
    source_extraction_id: UUID | None
    doc_kind: Kind | None
    number: str | None
    date: date | None
    cp_name: str | None
    cp_inn: str | None
    amount: str | None
    subject: str | None
    contract_refs: list[ReferenceView]
    supp_ref: ReferenceView | None
    invoice_ref: ReferenceView | None
    evidence: dict[str, str | None]
    evidence_checks: list[EvidenceCheck]
    valid: bool
    evidence_ok: bool
    cache_hit: bool
    is_current: bool
    preserved_error: bool
    error: str | None
    created_at: datetime


class FeaturesView(Public):
    num: float
    date: float
    inn: float
    name: float
    sem: float
    reference_index: int | None = None


class CandidateView(Public):
    id: UUID
    result_id: UUID
    source_id: UUID
    target_id: UUID
    link_type: Literal["изменение", "основание", "исполнение"]
    features: FeaturesView
    penalties: list[Penalty]
    score: float
    rank: int
    decision: Literal["recommendation", "review", "none"]
    source_evidence: Fields
    target_evidence: Fields


class SearchView(Public):
    id: UUID
    source_id: UUID
    model_config_id: UUID | None
    method: str
    config: LinkingConfig
    outcome: Outcome
    reasons: list[str]
    recommendations: list[UUID]
    error: str | None
    created_at: datetime
    input_hash: str
    working_copy: CopyOrigin | None
    candidates: list[CandidateView]


class LinkView(Public):
    id: UUID
    source_id: UUID
    target_id: UUID
    link_type: Literal["изменение", "основание", "исполнение"]
    origin: Literal["confirmed", "manual"]
    review_action_id: UUID
    suggestion_id: UUID | None
    created_at: datetime
    removed_at: datetime | None
    removal_action_id: UUID | None


class ReviewStateView(Public):
    document_id: UUID
    version: int
    status: Literal["open", "completed"]
    overrides: dict[str, Any]
    needs_review: bool


class SessionView(Public):
    id: UUID
    document_id: UUID
    result_id: UUID | None
    opened_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    input_revision: str
    working_copy: CopyOrigin | None


class ActionView(Public):
    id: UUID
    document_id: UUID
    session_id: UUID
    result_id: UUID | None
    suggestion_id: UUID | None
    chosen_target_id: UUID | None
    action: str
    reason: str | None
    idempotency_key: UUID
    opened_at: datetime
    decided_at: datetime
    duration_ms: int
    state_version: int


class StageView(Public):
    id: int
    stage: str
    duration_ms: int
    ok: bool
    error: str | None
    started_at: datetime


class PipelineView(Public):
    document_id: UUID
    batch_id: UUID
    stage: str
    job_id: UUID | None
    result_id: UUID | None
    error: str | None


class DocumentDetail(Public):
    document: DocumentSummary
    text: TextView | None
    extractions: list[ExtractionView]
    result: SearchView | None
    links: list[LinkView]
    review_state: ReviewStateView | None
    review_actions: list[ActionView]
    review_sessions: list[SessionView]
    stage_timings: list[StageView]
    pipeline: PipelineView | None
    working_copy: CopyOrigin | None


class UploadItem(Public):
    filename: str
    document_id: UUID | None
    error: str | None


class UploadResponse(Public):
    results: list[UploadItem]


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["failed", "search", "extract"] = "failed"
    model_id: UUID | None = None


class ProcessAccepted(Public):
    document_id: UUID
    job_ids: list[UUID]
    pipeline: PipelineView | None


class WorkingSetSummary(Public):
    id: UUID
    name: str
    corpus_version_id: UUID
    created_at: datetime


class CopyView(Public):
    document_id: UUID
    source_document_id: UUID
    source_result_id: UUID | None
    client_id: UUID


class WorkingSetView(WorkingSetSummary):
    copies: list[CopyView]


class CorpusVersionView(Public):
    id: UUID
    name: str
    imported_at: datetime


class ImportResult(Public):
    corpus_version_id: UUID
    reused: bool
    documents_imported: int
    links_imported: int
