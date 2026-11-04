"""Offline transport fixtures, never experimental observations."""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_models_extraction import Scripted, setup
from test_models_extraction import live as live

from app.config import Settings
from app.db import get_session
from app.models import CorpusVersion, Document, Job, LLMCall
from app.paid_resume import resume
from app.services.extraction import extract_document
from app.services.model_calls import ProcessingError, uncertain_retry_authorized
from app.services.providers import ProviderError


def test_explicit_retry_keeps_unknown_cost_and_reserve_and_never_repeats_success(
    live: Settings,
) -> None:
    doc, model, prompt, snapshot = setup(live, provider="yandex")
    with get_session(live.database_url) as session:
        version = CorpusVersion(
            name="fixture", fingerprint=str(uuid4()), seed=1, tune_share=1, report={}
        )
        session.add(version)
        session.flush()
        version_id = version.id
        session.get(Document, doc).corpus_version_id = version_id
        job = Job(
            type="extract",
            document_id=doc,
            model_config_id=model,
            status="error",
            payload={"snapshot": snapshot},
        )
        session.add(job)
        session.commit()
        job_id = job.id
    transport = Scripted(ProviderError("uncertain", uncertain=True))
    with pytest.raises(ProcessingError):
        extract_document(live, doc, model, prompt, snapshot, job_id, transport)
    with pytest.raises(ProcessingError, match="явного допуска"):
        resume(live, version_id, "User requests retry", False)
    result = resume(live, version_id, "User requests retry", True)
    assert result["requeued"] == 1 and result["new_network_calls"] == 0
    with get_session(live.database_url) as session:
        old = session.scalar(select(LLMCall))
        assert old.cost_rub is None and not old.reconciled
        assert uncertain_retry_authorized(old)
        live.budget_rub = old.reserved_rub
    success = Scripted('{"number":"А-1"}')
    with pytest.raises(ProcessingError, match="бюджета"):
        extract_document(live, doc, model, prompt, snapshot, job_id, success)
    assert success.count == 0
    live.budget_rub = Decimal(100)
    extract_document(live, doc, model, prompt, snapshot, job_id, success)
    assert success.count == 1
    assert resume(live, version_id, "User requests retry", True)["requeued"] == 0
    extract_document(live, doc, model, prompt, snapshot, job_id, success)
    assert success.count == 1
    with get_session(live.database_url) as session:
        calls = list(session.scalars(select(LLMCall).order_by(LLMCall.started_at)))
        assert len(calls) == 2
        assert calls[0].cost_rub is None and calls[0].status == "uncertain"
        assert calls[1].status == "success"
