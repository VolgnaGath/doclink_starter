"""Formula boundaries and real PostgreSQL provenance; fixtures are not effect evidence."""

import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from test_experiments import create
from test_experiments import matrix as matrix
from test_linking_acceptance import database as database
from test_linking_acceptance import sample as sample

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import ExperimentRun, ExperimentSeries, ReviewAction, ReviewSession, WorkingCopy
from app.services.effect import OUTCOMES, Scenario, calculate, defaults, markdown, replay, snapshot
from app.services.effect_data import local_estimate, run_data, scenario_defaults
from app.services.effect_measurements import measurement_audit
from app.services.experiments import execute_series, runs


def scenario(**changes: object) -> Scenario:
    values = {
        **defaults(),
        "recommendation_seconds": "60",
        "review_seconds": "300",
        "none_seconds": "420",
        "error_seconds": "600",
        "hourly_rate_rub": "600",
        "incoming_price_rub": "1",
        "contract_price_rub": "2",
        "extra_variable_rub": "0",
        "infrastructure_rub": "1000",
        "other_fixed_rub": "0",
        "contract_labor_rub": "0",
        "implementation_rub": "10000",
        **changes,
    }
    values["sources"] = {k: "Тестовое допущение, не измерение" for k in values}
    return Scenario.model_validate(values)


def test_units_full_document_times_and_separate_contract_preparation() -> None:
    result = calculate(scenario(), dict(recommendation=75, review=25, none=0, error=0))
    assert Decimal(result["asis_seconds"]) == 420
    assert Decimal(result["tobe_seconds"]) == 120
    assert float(result["asis_hours_month"]) == pytest.approx(191.3333333333)
    assert Decimal(result["contribution_rub_document"]) == 49
    assert Decimal(result["fixed_rub_month"]) == 1120
    assert Decimal(result["net_rub_month"]) == 79240
    assert float(result["break_even_documents"]) == pytest.approx(1120 / 49)
    assert float(result["payback_months"]) == pytest.approx(10000 / 79240)


def test_missing_is_not_zero_zero_share_needs_no_time_and_negative_is_preserved() -> None:
    counts = dict(recommendation=1, review=0, none=0, error=0)
    assert calculate(scenario(review_seconds=None), counts)["tobe_seconds"] == "60"
    missing = calculate(scenario(recommendation_seconds=None), counts)
    assert missing["tobe_seconds"] is missing["net_rub_month"] is None
    assert missing["missing_times"] == ["recommendation_seconds"]
    no_rate = calculate(scenario(hourly_rate_rub=None), counts)
    assert no_rate["saved_hours_month"] is not None and no_rate["net_rub_month"] is None
    negative = calculate(scenario(recommendation_seconds=900), counts)
    assert Decimal(negative["saved_hours_month"]) < 0 and Decimal(negative["net_rub_month"]) < 0
    assert negative["break_even_status"] == "no_finite_break_even"
    assert negative["payback_status"] == "no_payback"
    empty_flow = calculate(
        scenario(monthly_documents=0, monthly_contracts=0, contract_price_rub=None), counts
    )
    assert Decimal(empty_flow["net_rub_month"]) == -1000
    assert Decimal(empty_flow["saved_hours_month"]) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("v_decode", 0),
        ("v_prefill", -1),
        ("incomplete_share", "1.01"),
        ("hourly_rate_rub", "NaN"),
        ("monthly_documents", -1),
        ("review_seconds", -1),
    ],
)
def test_reject_invalid_units_and_nonfinite(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        scenario(**{field: value})


def test_snapshot_replay_and_markdown_are_one_immutable_calculation() -> None:
    data = {"counts": dict(recommendation=1, review=1, none=1, error=1)}
    saved = snapshot(scenario(), data, created_at="2026-11-04T00:00:00+00:00")
    assert replay(json.loads(json.dumps(saved))) == saved
    assert saved["calculation_hash"] in markdown(saved)
    saved["result"]["net_rub_month"] = "999"
    with pytest.raises(ValueError, match="хеш"):
        replay(saved)


def test_sources_and_four_document_denominators_are_required() -> None:
    with pytest.raises(ValidationError, match="источник"):
        Scenario()
    with pytest.raises(ValueError):
        calculate(scenario(), {o: 0 for o in OUTCOMES})
    with pytest.raises(ValueError):
        calculate(scenario(), {"recommendation": 2})


def test_local_time_is_calculated_from_only_open_weights_tokens() -> None:
    data = {
        "calls": [
            {
                "id": "q",
                "placement": "open-weights-cloud",
                "input_tokens": 100,
                "output_tokens": 20,
            },
            {"id": "y", "placement": "cloud", "input_tokens": 900, "output_tokens": 500},
        ]
    }
    result = local_estimate(scenario(v_prefill=50, v_decode=10), data)
    assert Decimal(result["seconds"]) == 4 and result["call_ids"] == ["q"]
    data["calls"][0]["input_tokens"] = None
    assert local_estimate(scenario(v_prefill=50, v_decode=10), data)["seconds"] is None


def test_shared_card_calls_and_corrections_count_once(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    from app.models import Extraction, LLMCall, ModelConfig, Prompt

    id = create(database, matrix[0])
    execute_series(database, id)
    with get_session(database.database_url) as session:
        run = next(r for r in runs(session, id) if r.status == "done")
        prompt = Prompt(name="effect-fixture", version=str(uuid4()), text="fixture", schema={})
        session.add(prompt)
        session.flush()
        model = session.scalar(select(ModelConfig))
        calls = []
        for status, cost in (("invalid", "2"), ("success", "3")):
            call = LLMCall(
                model_config_id=model.id,
                prompt_id=prompt.id,
                document_id=matrix[1],
                purpose="extract",
                input_hash=str(uuid4()),
                root_input_hash="shared-card-fixture",
                snapshot={"name": "frozen-fixture", "placement": "open-weights-cloud"},
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                cost_rub=Decimal(cost),
                paid=False,
                status=status,
                truncated=False,
            )
            session.add(call)
            calls.append(call)
        session.flush()
        for row in run.snapshot["extractions"]:
            session.get(Extraction, UUID(row["id"])).llm_call_id = calls[-1].id
        session.flush()
        data = run_data(session, run.id)
        assert len(data["calls"]) == 2
        assert all(
            c["model"] == "frozen-fixture" and c["placement"] == "open-weights-cloud"
            for c in data["calls"]
        )
        assert sum(c["physical_calls"] for c in data["costs"].values()) == 2
        assert sum(Decimal(c["source_cost_rub"]) for c in data["costs"].values()) == 5
        calls[0].cost_rub = None
        session.flush()
        data = run_data(session, run.id)
        assert data["costs"]["contracts"]["source_cost_rub"] is None
        assert Decimal(data["costs"]["contracts"]["known_cost_rub"]) == 3


def test_frozen_run_data_and_web_snapshot_downloads(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    id = create(database, matrix[0])
    execute_series(database, id)
    with get_session(database.database_url) as session:
        run = next(r for r in runs(session, id) if r.method == "combined" and r.ablation == "none")
        run_id = run.id
        before = copy.deepcopy(session.get(ExperimentSeries, id).dataset)
        data = run_data(session, run_id)
        assert sum(data["counts"].values()) == data["n"] == 1
        assert len(data["results"]) == 1
        assert "SECRET_PATH" not in json.dumps(data)
        assert not data["measurements"]["usable_as_measurement"]
        assert data["costs"]["contracts"]["documents"] == 1
        assert scenario_defaults(data)["incoming_price_rub"] == "0"
        assert "incoming_price_rub" not in scenario_defaults(data, "server")
        assert local_estimate(scenario(), data)["seconds"] is None
    with TestClient(create_app(database)) as client:
        assert client.get("/effect", follow_redirects=False).status_code == 303
        client.post(
            "/login", data={"username": database.app_user, "password": database.app_password}
        )
        response = client.get(f"/effect?run_id={run_id}")
        assert response.status_code == 200 and "Расчёт эффекта" in response.text
        values = scenario().model_dump(mode="json")
        form = {k: str(v) for k, v in values.items() if k != "sources" and v is not None}
        form.update({f"source_{k}": v for k, v in values["sources"].items()})
        form["run_id"] = str(run_id)
        result = client.post("/effect", data=form)
        assert result.status_code == 200 and 'id="effect-result"' in result.text
        import re
        from html import unescape

        raw = unescape(re.search(r'name="snapshot_json" value="([^"]+)"', result.text)[1])
        for format in ("json", "md"):
            downloaded = client.post(
                "/effect/export", data={"snapshot_json": raw, "format": format}
            )
            assert (
                downloaded.status_code == 200
                and json.loads(raw)["calculation_hash"] in downloaded.text
            )
        malformed = client.post("/effect/export", data={"snapshot_json": "{}", "format": "json"})
        assert malformed.status_code == 422
        assert (
            client.post(
                "/effect/export", data={"snapshot_json": "[]", "format": "json"}
            ).status_code
            == 422
        )
        form["mode"] = "server"
        for field in ("incoming_price_rub", "contract_price_rub"):
            form[f"source_{field}"] = "Прогноз по исходным физическим вызовам incoming"
        assert "Для сервера задайте" in client.post("/effect", data=form).text
        form["mode"] = "cloud"
        form["incoming_price_rub"] = ""
        no_price = client.post("/effect", data=form)
        raw_missing = unescape(re.search(r'name="snapshot_json" value="([^"]+)"', no_price.text)[1])
        assert json.loads(raw_missing)["inputs"]["incoming_price_rub"] is None
    with get_session(database.database_url) as session:
        assert session.get(ExperimentSeries, id).dataset == before
        run = session.get(ExperimentRun, run_id)
        run.status = "NO_FEASIBLE_CONFIGURATION"
        session.flush()
        with pytest.raises(ValueError, match="завершённый"):
            run_data(session, run_id)


def test_measurements_first_session_actions_outcomes_and_exclusions(
    database: Settings, matrix: tuple[UUID, UUID]
) -> None:
    from app.models import Document, WorkingSet

    id = create(database, matrix[0])
    execute_series(database, id)
    with get_session(database.database_url) as session:
        run = next(r for r in runs(session, id) if r.status == "done")
        data = run_data(session, run.id)
        source = data["results"][0]
        original = session.get(Document, UUID(source["document_id"]))
        work = WorkingSet(
            corpus_version_id=matrix[0],
            name="fixture",
            idempotency_key=uuid4(),
            request_hash="fixture",
        )
        session.add(work)
        session.flush()
        # Existing corpus document used only as a fixture copy; origin validation patched below.
        session.add(
            WorkingCopy(
                document_id=original.id,
                working_set_id=work.id,
                source_document_id=original.id,
                source_result_id=UUID(source["result_id"]),
                source_result_current=True,
            )
        )
        now = datetime.now(UTC)
        review = ReviewSession(
            document_id=original.id,
            result_id=UUID(source["result_id"]),
            context={
                "provenance": {
                    "working_copy": {
                        "initial_inputs": True,
                        "source_result_id": source["result_id"],
                    }
                }
            },
            opened_at=now,
            completed_at=now + timedelta(seconds=30),
            duration_ms=30000,
        )
        session.add(review)
        session.flush()
        for index, action in enumerate(("confirm", "complete")):
            session.add(
                ReviewAction(
                    document_id=original.id,
                    session_id=review.id,
                    result_id=review.result_id,
                    action=action,
                    idempotency_key=uuid4(),
                    request_hash="fixture",
                    opened_at=now,
                    decided_at=now + timedelta(seconds=20 + index * 10),
                    duration_ms=20000 + index * 10000,
                    state_version=index,
                )
            )
        repeated = ReviewSession(
            document_id=original.id,
            result_id=review.result_id,
            context=review.context,
            opened_at=now + timedelta(minutes=1),
            completed_at=None,
            duration_ms=None,
        )
        session.add(repeated)
        session.flush()
        from unittest.mock import patch

        with patch(
            "app.services.effect_measurements.copy_origin", return_value={"initial_inputs": True}
        ):
            audit = measurement_audit(
                session, run.id, working_set_id=work.id, selected_result_ids=[source["result_id"]]
            )
            assert audit["groups"][source["outcome"]]["mean_ms"] == 30000
            assert len(audit["groups"][source["outcome"]]["action_ids"]) == 2
            assert audit["exclusions"] == {"repeat_session": 1}
            review.completed_at = None
            session.flush()
            audit = measurement_audit(
                session, run.id, working_set_id=work.id, selected_result_ids=[source["result_id"]]
            )
            assert audit["groups"][source["outcome"]]["n"] == 0
            assert audit["exclusions"] == {"unfinished": 1, "repeat_session": 1}


def test_portable_artifacts_restore_real_fk_graph_and_reject_nonempty_target(
    database: Settings, matrix: tuple[UUID, UUID], tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    from pathlib import Path

    from sqlalchemy import text

    from app.delivery_artifacts import export_bundle, restore_bundle
    from app.models import Document, DocumentText, Job, StageTiming

    root = Path(str(tmp_path))
    id = create(database, matrix[0])
    execute_series(database, id)
    with get_session(database.database_url) as session:
        for job in session.scalars(select(Job)):
            job.status = "done"
        for doc in session.scalars(select(Document)):
            raw = session.get(DocumentText, doc.id).text.encode()
            path = root / str(doc.id)
            path.write_bytes(raw)
            doc.storage_path, doc.sha256, doc.is_anonymized = (
                str(path),
                hashlib.sha256(raw).hexdigest(),
                True,
            )
        session.commit()
        bundle = export_bundle(session, id)
        assert "anon_maps" not in bundle["tables"] and "review_actions" not in bundle["tables"]
        assert len(bundle["tables"]["experiment_runs"]) == 8
        with pytest.raises(ValueError, match="отдельном"):
            restore_bundle(session, bundle, root / "blobs")
        monkeypatch.setenv("DELIVERY_ACCEPTANCE_ENABLED", "1")
        with pytest.raises(ValueError, match="пустая"):
            restore_bundle(session, bundle, root / "blobs")
        session.execute(
            text("TRUNCATE clients, corpus_versions, embeddings, model_configs, prompts CASCADE")
        )
        session.commit()
        report = restore_bundle(session, bundle, root / "blobs")
        session.commit()
        assert report["new_llm_calls"] == 0
        assert session.get(ExperimentSeries, id).dataset["dataset_hash"] == bundle["dataset_hash"]
        assert (
            run_data(session, next(r.id for r in runs(session, id) if r.status == "done"))["n"] == 1
        )
        for doc in session.scalars(select(Document)):
            assert hashlib.sha256(Path(doc.storage_path).read_bytes()).hexdigest() == doc.sha256
        maximum = max(session.scalars(select(StageTiming.id)))
        timing = StageTiming(document_id=matrix[1], stage="fixture", duration_ms=1, ok=True)
        session.add(timing)
        session.flush()
        assert timing.id > maximum
