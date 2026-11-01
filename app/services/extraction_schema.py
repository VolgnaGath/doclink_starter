"""Wire schema and deterministic validation, shared by local and remote extractors."""

import re
from datetime import datetime
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: str | None = None
    date: str | None = Field(default=None, pattern=r"^\d{2}\.\d{2}\.\d{4}$")
    quote: str

    @field_validator("date")
    @classmethod
    def calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
                raise ValueError("Дата должна быть ДД.ММ.ГГГГ")
            datetime.strptime(value, "%d.%m.%Y")
        return value


class ExtractedFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_kind: Literal["договор", "допсоглашение", "счёт", "акт", "УПД", "другое"] | None = None
    number: str | None = None
    date: str | None = Field(default=None, pattern=r"^\d{2}\.\d{2}\.\d{4}$")
    counterparty_name: str | None = None
    counterparty_inn: str | None = None
    amount: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    subject: str | None = None
    contract_refs: list[Reference] = Field(default_factory=list)
    supp_ref: Reference | None = None
    invoice_ref: Reference | None = None
    evidence: dict[Literal["number", "date", "counterparty", "amount"], str | None] = Field(
        default_factory=dict
    )

    _date = field_validator("date")(Reference.calendar_date.__func__)


PREPARATION_VERSION = "extract-input-v1"
GAP = "\n[... ПРОПУЩЕН ФРАГМЕНТ ...]\n"


def truncate(text: str) -> tuple[str, list[str], bool]:
    if len(text) <= 8500:
        return text, [text], False
    parts = [text[:6000], text[-2500:]]
    return GAP.join(parts), parts, True


def match_quote(quote: str, parts: list[str]) -> dict[str, object]:
    """Never match across the truncation gap. Offsets refer to sent text segments."""
    if not quote or not quote.strip():
        return {"ok": False, "method": "empty", "start": None, "end": None}
    offset = 0
    for part_index, part in enumerate(parts):
        start = part.find(quote)
        method = "exact"
        end = start + len(quote)
        if start < 0:
            # Keep original offsets while collapsing whitespace.
            tokens = list(re.finditer(r"\S+", part))
            normalized = " ".join(m.group() for m in tokens)
            needle = " ".join(quote.split())
            index = normalized.find(needle)
            if index >= 0:
                positions = []
                for token in tokens:
                    if positions:
                        positions.append(token.start() - 1)
                    positions.extend(range(token.start(), token.end()))
                start, end = positions[index], positions[index + len(needle) - 1] + 1
                method = "whitespace"
            elif 20 <= len(needle) <= 500:
                # Bounded windows: long, mostly identical quotations only.
                for m in re.finditer(re.escape(needle[:8]), normalized):
                    window = normalized[m.start() : m.start() + len(needle)]
                    if SequenceMatcher(None, needle, window, autojunk=False).ratio() >= 0.96:
                        positions = []
                        for token in tokens:
                            if positions:
                                positions.append(token.start() - 1)
                            positions.extend(range(token.start(), token.end()))
                        start = positions[m.start()]
                        end = positions[m.start() + len(window) - 1] + 1
                        method = "fuzzy"
                        break
        if start >= 0:
            return {
                "ok": True,
                "method": method,
                "segment": part_index,
                "start": offset + start,
                "end": offset + end,
                "matched": part[start:end],
            }
        offset += len(part) + len(GAP)
    return {"ok": False, "method": "not_found", "start": None, "end": None}


def check_evidence(fields: ExtractedFields, parts: list[str]) -> list[dict[str, object]]:
    quotes = {name: quote for name, quote in fields.evidence.items() if quote is not None}
    for index, ref in enumerate(fields.contract_refs):
        quotes[f"contract_refs.{index}"] = ref.quote
    for name in ("supp_ref", "invoice_ref"):
        if ref := getattr(fields, name):
            quotes[name] = ref.quote
    # Missing evidence for extracted fields must not count as verified.
    for name, value in (
        ("number", fields.number),
        ("date", fields.date),
        ("amount", fields.amount),
        ("counterparty", fields.counterparty_name or fields.counterparty_inn),
    ):
        if value is not None:
            quotes.setdefault(name, None)
    return [
        {"field": name, "quote": quote, **match_quote(quote or "", parts)}
        for name, quote in quotes.items()
    ]


def regex_extract(text: str) -> ExtractedFields:
    """Conservative baseline: header and explicit dated references, never inferred values."""
    fields = ExtractedFields()
    kinds = [
        (r"дополнительное\s+соглашение|допсоглашение", "допсоглашение"),
        (r"универсальный\s+передаточный\s+документ|УПД", "УПД"),
        (r"сч[её]т", "счёт"),
        (r"акт", "акт"),
        (r"договор", "договор"),
    ]
    header = text[:600]
    for pattern, kind in kinds:
        m = re.search(rf"^\s*(?:{pattern})\b[^\n]{{0,150}}", header, re.I | re.M)
        if m:
            fields.doc_kind = kind
            if n := re.search(r"№\s*([^\s,]+)", m.group()):
                fields.number = n.group(1)
                fields.evidence["number"] = m.group()
            if d := re.search(r"\b\d{2}\.\d{2}\.\d{4}\b", m.group()):
                try:
                    fields.date = Reference.calendar_date(d.group())
                    fields.evidence["date"] = m.group()
                except ValueError:
                    pass
            break
    if fields.doc_kind != "договор":
        for m in re.finditer(
            r"договор[ауеом]*\s*№\s*([^\s,]+)\s+от\s+(\d{2}\.\d{2}\.\d{4})", text, re.I
        ):
            try:
                fields.contract_refs.append(Reference(number=m[1], date=m[2], quote=m[0]))
            except ValueError:
                pass
    return fields
