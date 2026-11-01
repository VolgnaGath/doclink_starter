"""Read-only acceptance snapshot; never export document text, gold or anon_maps."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from app.config import Settings
from app.db import get_session
from app.models import CorpusVersion, Document, Extraction, Job, LLMCall, ModelConfig
from app.services.model_calls import fingerprint


def audit(settings: Settings, version_id: UUID, protocol_hash: str) -> dict[str, Any]:
    with get_session(settings.database_url) as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        version = session.get(CorpusVersion, version_id)
        if version is None:
            raise ValueError("Версия корпуса не найдена")
        protocol = version.report["extraction_protocols"][protocol_hash]
        if fingerprint({"models": protocol["models"], "preparation": protocol["preparation"]}) != (
            protocol_hash
        ):
            raise ValueError("Хеш протокола не совпадает со снимками")
        models = {m.name: m for m in session.scalars(select(ModelConfig))}
        documents = {
            d.id: d
            for d in session.scalars(
                select(Document).where(Document.corpus_version_id == version_id)
            )
        }
        model_ids = [models[s["name"]].id for s in protocol["models"]]
        results = list(
            session.scalars(
                select(Extraction).where(
                    Extraction.document_id.in_(documents),
                    Extraction.model_config_id.in_(model_ids),
                    Extraction.is_current,
                )
            )
        )
        calls = list(session.scalars(select(LLMCall).order_by(LLMCall.started_at, LLMCall.id)))
        groups: dict[tuple[str, str, str, str], list[LLMCall]] = defaultdict(list)
        for call in calls:
            phase = documents[call.document_id].split if call.document_id in documents else "other"
            groups[
                (call.snapshot["name"], call.snapshot["prompt_version"], phase, call.status)
            ].append(call)
        consumption = []
        for (name, prompt, phase, status), rows in sorted(groups.items()):
            cached = [r.snapshot.get("usage_details", {}).get("cached_input_tokens") for r in rows]
            versions = Counter(
                json.loads(r.response_raw).get("model", "unspecified")
                for r in rows
                if r.response_raw
            )
            consumption.append(
                {
                    "model": name,
                    "prompt": prompt,
                    "phase": phase,
                    "status": status,
                    "attempts": len(rows),
                    "input_tokens": sum(r.input_tokens or 0 for r in rows)
                    if any(r.input_tokens is not None for r in rows)
                    else None,
                    "output_tokens": sum(r.output_tokens or 0 for r in rows)
                    if any(r.output_tokens is not None for r in rows)
                    else None,
                    "missing_usage_attempts": sum(
                        r.input_tokens is None or r.output_tokens is None for r in rows
                    ),
                    "cached_input_tokens": sum(v or 0 for v in cached)
                    if any(v is not None for v in cached)
                    else None,
                    "known_cost_rub": str(
                        sum((r.cost_rub or Decimal(0) for r in rows), Decimal(0))
                    ),
                    "unknown_cost_attempts": sum(r.cost_rub is None for r in rows),
                    "truncated_attempts": sum(r.truncated for r in rows),
                    "response_models": dict(versions),
                }
            )
        summaries = {}
        errors = []
        for snapshot in protocol["models"]:
            model = models[snapshot["name"]]
            subsets = {}
            for phase in ("all", "tune", "test"):
                ids = {d.id for d in documents.values() if phase == "all" or d.split == phase}
                rows = [
                    r for r in results if r.model_config_id == model.id and r.document_id in ids
                ]
                checks = [c for r in rows for c in r.evidence_checks]
                supplied = [c for c in checks if c.get("quote")]
                subsets[phase] = {
                    "expected": len(ids),
                    "results": len(rows),
                    "valid": sum(r.valid for r in rows),
                    "errors": sum(not r.valid for r in rows),
                    "in_scope_results": sum(documents[r.document_id].in_scope for r in rows),
                    "in_scope_valid": sum(
                        r.valid and documents[r.document_id].in_scope for r in rows
                    ),
                    "snapshot_mismatches": sum(r.snapshot != snapshot for r in rows),
                    "evidence_ok": sum(r.evidence_ok for r in rows),
                    "supplied_quotes": len(supplied),
                    "found_quotes": sum(bool(c["ok"]) for c in supplied),
                    "missing_evidence": sum(not c.get("quote") for c in checks),
                }
            summaries[model.name] = subsets
            for row in results:
                if row.model_config_id == model.id and not row.valid:
                    call = next((c for c in calls if c.id == row.llm_call_id), None)
                    errors.append(
                        {
                            "document_id": str(row.document_id),
                            "model": model.name,
                            "split": documents[row.document_id].split,
                            "error": row.error,
                            "validation": json.loads(call.error)
                            if call and call.status == "invalid" and call.error
                            else None,
                        }
                    )
        provider_totals = {}
        for provider in ("yandex", "gigachat"):
            rows = [c for c in calls if c.snapshot["provider"] == provider]
            provider_totals[provider] = {
                "known_cost_rub": str(sum((r.cost_rub or Decimal(0) for r in rows), Decimal(0))),
                "unknown_cost_attempts": sum(r.cost_rub is None for r in rows),
                "started_attempts": sum(r.status == "started" for r in rows),
                "limit_rub": str(getattr(settings, f"{provider}_budget_rub")),
            }
        active_jobs = list(
            session.scalars(
                select(Job.id).where(
                    Job.document_id.in_(documents),
                    Job.model_config_id.in_(model_ids),
                    Job.status.in_(["queued", "running"]),
                )
            )
        )
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "corpus_version": str(version_id),
            "corpus_fingerprint": version.fingerprint,
            "protocol_hash": protocol_hash,
            "protocol": protocol,
            "results": summaries,
            "errors": errors,
            "active_jobs": len(active_jobs),
            "consumption_all_history": consumption,
            "provider_totals_all_history": provider_totals,
            "notes": [
                "other includes technical smoke and other corpus versions; "
                "usage sums exclude nulls",
                "valid means schema-valid, not extraction accuracy; no gold metrics computed",
                "cost_rub is tariff equivalent; cash debit has not been reconciled",
            ],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Снимок живой приёмки без текстов и эталона")
    parser.add_argument("--corpus-version", type=UUID, required=True)
    parser.add_argument("--protocol-hash", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(Settings(), args.corpus_version, args.protocol_hash)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Отчёт сохранён: {args.output}")


if __name__ == "__main__":
    main()
