"""Read-only projection of frozen experiment provenance for the effect calculator."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Document,
    ExperimentResult,
    ExperimentRun,
    ExperimentSeries,
    Extraction,
    LinkingResult,
    ModelConfig,
    WorkingCopy,
)
from app.services.effect import OUTCOMES, Scenario, defaults
from app.services.effect_measurements import measurement_audit, validate_protocol
from app.services.experiment_reporting import physical_calls, resources
from app.services.model_calls import fingerprint


def run_data(
    session: Session, run_id: UUID, protocol: dict[str, Any] | None = None
) -> dict[str, Any]:
    run = session.get(ExperimentRun, run_id)
    if run is None or run.status != "done":
        raise ValueError("Выберите завершённый прогон")
    series = session.get(ExperimentSeries, run.series_id)
    dataset = series.dataset
    saved = next((r for r in dataset.get("runs", []) if r["id"] == str(run.id)), None)
    if not saved or series.status != "done" or saved["protocol_hash"] != run.protocol_hash:
        raise ValueError("Нет согласованного замороженного dataset серии")
    if (
        fingerprint({k: v for k, v in dataset.items() if k != "dataset_hash"})
        != dataset["dataset_hash"]
    ):
        raise ValueError("Хеш dataset не совпадает")
    rows = list(
        session.execute(
            select(ExperimentResult, LinkingResult)
            .join(LinkingResult, LinkingResult.id == ExperimentResult.result_id)
            .where(ExperimentResult.run_id == run.id)
            .order_by(ExperimentResult.document_id)
        )
    )
    test_ids = set(
        session.scalars(
            select(Document.id).where(
                Document.id.in_([a.document_id for a, _ in rows]), Document.split == "test"
            )
        )
    )
    test = [(a, r) for a, r in rows if a.document_id in test_ids]
    if not test:
        raise ValueError("В прогоне нет test-документов")
    counts = {o: sum(r.outcome == o for _, r in test) for o in OUTCOMES}
    source_ids = {str(a.document_id) for a, _ in test}
    # Candidate scope comes from the frozen method input, never current gold/cards.
    contract_ids = {
        str(c["card"]["document_id"])
        for _, r in test
        for c in r.input_snapshot.get("candidates", [])
    }
    extraction_rows = list(
        session.scalars(
            select(Extraction).where(
                Extraction.id.in_([UUID(e["id"]) for e in saved["snapshot"]["extractions"]])
            )
        )
    )
    groups = {
        "contracts": [e for e in extraction_rows if str(e.document_id) in contract_ids],
        "incoming": [e for e in extraction_rows if str(e.document_id) in source_ids],
        "other": [
            e for e in extraction_rows if str(e.document_id) not in contract_ids | source_ids
        ],
    }
    # Fixed precedence allocates shared physical attempts exactly once.
    seen = set()
    costs = {}
    call_rows = []
    for name in ("contracts", "incoming", "other"):
        calls = [c for c in physical_calls(session, groups[name]) if c.id not in seen]
        seen.update(c.id for c in calls)
        denominator = (
            len(contract_ids)
            if name == "contracts"
            else len(test)
            if name == "incoming"
            else len({e.document_id for e in groups[name]})
        )
        costs[name] = resources(calls, denominator)
        for c in calls:
            model = session.get(ModelConfig, c.model_config_id)
            call_rows.append(
                {
                    "id": str(c.id),
                    "scope": name,
                    "model_id": str(c.model_config_id),
                    "model": c.snapshot.get("name", model.name),
                    "placement": c.snapshot.get("placement", model.placement),
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "cost_rub": str(c.cost_rub) if c.cost_rub is not None else None,
                    "status": c.status,
                }
            )
    working_set_id, selected_ids = None, None
    working_set_ids, selected_document_ids = None, None
    if protocol:
        validate_protocol(protocol)
        if (
            protocol["run_id"] != str(run.id)
            or protocol["dataset_hash"] != dataset["dataset_hash"]
            or protocol["run_protocol_hash"] != run.protocol_hash
        ):
            raise ValueError("Протокол замеров относится к другому прогону")
        batches = protocol.get("batches", [protocol])
        working_set_ids = [UUID(batch["working_set_id"]) for batch in batches]
        if len(batches) == 1:
            working_set_id = working_set_ids[0]
        actual_results = {str(r.id): (str(a.document_id), r.outcome) for a, r in test}
        selected_document_ids = []
        for batch in batches:
            for doc in batch["documents"]:
                copy = session.get(WorkingCopy, UUID(doc["document_id"]))
                expected = actual_results.get(doc["source_result_id"])
                if (
                    copy is None
                    or str(copy.working_set_id) != batch["working_set_id"]
                    or str(copy.source_result_id) != doc["source_result_id"]
                    or expected != (str(copy.source_document_id), doc["outcome"])
                ):
                    raise ValueError("Документ не соответствует протоколу замеров")
                selected_document_ids.append(copy.document_id)
        selected_ids = [r["source_result_id"] for r in protocol["documents"]]
    return {
        "run_id": str(run.id),
        "series_id": str(series.id),
        "variant": run.variant,
        "corpus_version_id": str(series.corpus_version_id),
        "protocol_hash": run.protocol_hash,
        "dataset_hash": dataset["dataset_hash"],
        "reused_test": run.reused_test,
        "selected_by_tune": run.model_config_id == series.selected_model_id
        and run.method == "combined"
        and run.ablation == "none",
        "counts": counts,
        "n": len(test),
        "results": [
            {
                "document_id": str(a.document_id),
                "result_id": str(r.id),
                "outcome": r.outcome,
                "recommendations": len(r.recommendations),
            }
            for a, r in test
        ],
        "costs": costs,
        "calls": sorted(call_rows, key=lambda c: c["id"]),
        "extraction_ids": sorted(str(e.id) for e in extraction_rows),
        "contract_document_ids": sorted(contract_ids),
        "measurements": measurement_audit(
            session,
            run.id,
            working_set_id=working_set_id,
            selected_result_ids=selected_ids,
            working_set_ids=working_set_ids,
            selected_document_ids=selected_document_ids,
        ),
        "measurement_protocol": protocol,
        "test_metrics": saved["report"]["metrics"]["test"]["all"],
        "cost_note": (
            "Исходные вычисления: тарифный эквивалент, не платёж. "
            "Расчёт новых вызовов не делает. Кэш не прогноз бесплатной эксплуатации."
        ),
        "placement_label": "Qwen: открытые веса, облачное размещение",
    }


def scenario_defaults(data: dict[str, Any], mode: str = "cloud") -> dict[str, Any]:
    values = {**defaults(), "mode": mode}
    if mode == "cloud":
        for field, scope in (
            ("incoming_price_rub", "incoming"),
            ("contract_price_rub", "contracts"),
        ):
            cost = data["costs"][scope]["source_cost_per_document_rub"]
            if cost is not None:
                values[field] = cost
                values["sources"][field] = (
                    f"Прогноз по исходным физическим вызовам {scope}; "
                    f"run={data['run_id']}; dataset={data['dataset_hash']}"
                )
    if data["measurements"]["usable_as_measurement"]:
        for outcome, group in data["measurements"]["groups"].items():
            if group["mean_ms"] is not None:
                field = f"{outcome}_seconds"
                values[field] = str(Decimal(str(group["mean_ms"])) / 1000)
                values["sources"][field] = (
                    f"Среднее завершённых сессий {data['measurements']['protocol']}: "
                    + ", ".join(group["session_ids"])
                )
    return values


def local_estimate(scenario: Scenario, data: dict[str, Any]) -> dict[str, Any]:
    calls = [c for c in data["calls"] if c["placement"] == "open-weights-cloud"]
    seconds = None
    if (
        calls
        and scenario.v_prefill
        and scenario.v_decode
        and all(c["input_tokens"] is not None and c["output_tokens"] is not None for c in calls)
    ):
        seconds = str(
            sum(
                (
                    Decimal(c["input_tokens"]) / scenario.v_prefill
                    + Decimal(c["output_tokens"]) / scenario.v_decode
                    for c in calls
                ),
                Decimal(0),
            )
        )
    return {
        "seconds": seconds,
        "call_ids": [c["id"] for c in calls],
        "label": "Расчётное локальное время; не измерение GPU; все области исходных Qwen-вызовов",
    }
