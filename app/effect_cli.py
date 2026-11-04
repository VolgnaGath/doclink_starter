"""Snapshots and pre-registered human measurement copies. No paid calls."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import WorkingCopy
from app.services.effect import OUTCOMES, Scenario, markdown, replay, snapshot
from app.services.effect_data import local_estimate, run_data, scenario_defaults
from app.services.effect_measurements import validate_protocol
from app.services.model_calls import fingerprint
from app.services.working_sets import WorkingSetCreate, create_working_set


def prepare_measurements(session: Session, run_id: UUID, per_outcome: int = 3) -> dict[str, Any]:
    if not 1 <= per_outcome <= 30:
        raise ValueError("Число документов каждого исхода: от 1 до 30")
    data = run_data(session, run_id)
    selected = []
    for outcome in OUTCOMES:
        selected.extend(
            sorted(
                (r for r in data["results"] if r["outcome"] == outcome),
                key=lambda r: r["result_id"],
            )[:per_outcome]
        )
    work = create_working_set(
        session,
        WorkingSetCreate(
            name=f"Замеры effect-review-v1 {run_id}, k={per_outcome}",
            corpus_version_id=UUID(data["corpus_version_id"]),
            document_ids=[UUID(r["document_id"]) for r in selected],
            result_ids=[UUID(r["result_id"]) for r in selected],
            idempotency_key=uuid5(run_id, f"effect-review-v1:{per_outcome}"),
        ),
    )
    copies = {
        str(c.source_result_id): c
        for c in session.scalars(select(WorkingCopy).where(WorkingCopy.working_set_id == work.id))
    }
    content = {
        "version": "effect-review-v1",
        "run_id": str(run_id),
        "dataset_hash": data["dataset_hash"],
        "run_protocol_hash": data["protocol_hash"],
        "registered_at": work.created_at.isoformat(),
        "working_set_id": str(work.id),
        "per_outcome": per_outcome,
        "selection_rule": (
            "Первые k test-результатов каждого исхода по ID; "
            "порядок исходов recommendation/review/none/error"
        ),
        "session_rule": "Первая открытая сессия; незавершённую/изменённую не заменять повтором",
        "documents": [
            {
                "source_result_id": r["result_id"],
                "outcome": r["outcome"],
                "document_id": str(copies[r["result_id"]].document_id),
                "url": f"/review/{copies[r['result_id']].document_id}",
            }
            for r in selected
        ],
    }
    return {**content, "protocol_hash": fingerprint(content)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["snapshot", "replay", "prepare-measurements"])
    parser.add_argument("--run", type=UUID)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-outcome", type=int, default=3)
    args = parser.parse_args()
    if args.command == "replay":
        saved = replay(json.loads(args.input.read_text()))
    else:
        if args.run is None:
            parser.error("--run обязателен")
        with get_session(Settings().database_url) as session:
            if args.command == "prepare-measurements":
                saved = prepare_measurements(session, args.run, args.per_outcome)
                session.commit()
                if args.output.exists():
                    previous = json.loads(args.output.read_text())
                    validate_protocol(previous)
                    if previous != saved:
                        raise ValueError("Нельзя перезаписать другой протокол")
            else:
                protocol = json.loads(args.protocol.read_text()) if args.protocol else None
                data = run_data(session, args.run, protocol)
                values = (
                    json.loads(args.input.read_text()) if args.input else scenario_defaults(data)
                )
                scenario = Scenario.model_validate(values)
                data["local_estimate"] = local_estimate(scenario, data)
                saved = snapshot(scenario, data, created_at=datetime.now(UTC).isoformat())
    args.output.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n")
    if args.command != "prepare-measurements":
        args.output.with_suffix(".md").write_text(markdown(saved))
    print(
        json.dumps(
            {
                k: saved[k]
                for k in ("calculation_hash", "result", "protocol_hash", "documents")
                if k in saved
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
