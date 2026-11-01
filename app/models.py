"""Persistence models. Evaluation metadata never supplies working document fields."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    func,
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
