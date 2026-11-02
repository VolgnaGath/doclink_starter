"""Allowlisted engine inputs: no ORM objects, corpus labels or filesystem paths."""

from datetime import date as CalendarDate
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["договор", "допсоглашение", "счёт", "акт", "УПД", "другое"]
Outcome = Literal["recommendation", "review", "none", "error"]
METHOD_VERSION = "linking-v1"


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Card(Value):
    document_id: str
    client_id: str
    corpus_version_id: str | None
    kind: Kind | None = None
    date: CalendarDate | None = None
    error: str | None = None


class ContractReference(Value):
    number: str | None = None
    date: CalendarDate | None = None
    verified: bool = False


class Fields(Value):
    number: str | None = None
    inn: str | None = None
    name: str | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    references: tuple[ContractReference, ...] = ()
    # Kept independently of scores; unrelated missing evidence is not a global veto.
    number_verified: bool = False
    counterparty_verified: bool = False
    amount_verified: bool = False
    error: str | None = None


class Document(Value):
    card: Card
    fields: Fields = Field(default_factory=Fields)
    text: str = ""


class Similarity(Value):
    target_id: str
    # Cosine of normalized vectors, clipped to [0, 1] only when used as a feature.
    cosine: float = Field(ge=-1, le=1)


class LinkingInput(Value):
    source: Document
    candidates: tuple[Document, ...] = ()
    similarities: tuple[Similarity, ...] = ()
    semantic_error: str | None = None

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        ids = [doc.card.document_id for doc in self.candidates]
        scores = [score.target_id for score in self.similarities]
        if len(ids) != len(set(ids)) or len(scores) != len(set(scores)):
            raise ValueError("Повтор идентификатора кандидата или смысловой оценки")
        return self


class Weights(Value):
    num: float = Field(default=0.45, ge=0, le=1)
    date: float = Field(default=0.15, ge=0, le=1)
    inn: float = Field(default=0.15, ge=0, le=1)
    name: float = Field(default=0.05, ge=0, le=1)
    sem: float = Field(default=0.20, ge=0, le=1)

    @model_validator(mode="after")
    def unit_sum(self) -> Self:
        if abs(sum(self.model_dump().values()) - 1) > 1e-9:
            raise ValueError("Сумма весов должна быть 1")
        return self


class LinkingConfig(Value):
    method: Literal["rules", "semantic", "combined"] = "combined"
    tau_a: float = Field(ge=0, le=1)
    tau_r: float = Field(ge=0, le=1)
    weights: Weights = Field(default_factory=Weights)
    amount_penalty: float = Field(default=0.20, ge=0, le=1)
    invalid_inn_penalty: float = Field(default=0.05, ge=0, le=1)
    checks: bool = True
    use_semantic: bool = True
    version: Literal["linking-v1"] = METHOD_VERSION

    @model_validator(mode="after")
    def ordered_thresholds(self) -> Self:
        if self.tau_r > self.tau_a:
            raise ValueError("tau_r должна быть не больше tau_a")
        return self


class Features(Value):
    num: float = 0
    date: float = 0
    inn: float = 0
    name: float = 0
    sem: float = 0
    reference_index: int | None = None


class Penalty(Value):
    reason: str
    value: float


class CandidateScore(Value):
    target_id: str
    features: Features
    penalties: tuple[Penalty, ...] = ()
    score: float
    rank: int = 0
    decision: Literal["recommendation", "review", "none"] = "none"
    link_type: Literal["изменение", "основание", "исполнение"]
    source_evidence: Fields
    target_evidence: Fields


class LinkingResult(Value):
    outcome: Outcome
    reasons: tuple[str, ...]
    recommendations: tuple[str, ...] = ()
    # Full ranking, not top-3: display limits belong to the caller.
    candidates: tuple[CandidateScore, ...] = ()
    config: LinkingConfig
