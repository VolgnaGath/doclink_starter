import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ModelConfig
from app.provider_admin import check_gigachat, configure_yandex, download_ca, install_ca
from app.services.providers import ChatProvider, ProviderError


@pytest.mark.parametrize("balance_status", [200, 403, 401])
def test_account_metadata_never_generates_or_exposes_tokens(
    settings: Settings, balance_status: int
) -> None:
    settings.gigachat_credentials = "private-auth"
    requests = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("oauth"):
            return httpx.Response(
                200, json={"access_token": "private-access", "expires_at": 9999999999999}
            )
        if request.url.path.endswith("models"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "GigaChat-2"}, {"id": "Embeddings"}],
                    "echo": "private-access",
                },
            )
        return httpx.Response(
            balance_status,
            json={"balance": [{"usage": "GigaChat", "value": 1000000}], "echo": "private-auth"},
        )

    adapter = ChatProvider(
        settings, "gigachat", "", {}, httpx.Client(transport=httpx.MockTransport(transport))
    )
    result = adapter.inspect_account()
    assert requests == [("POST", "/api/v2/oauth"), ("GET", "/v1/models"), ("GET", "/v1/balance")]
    assert result["models"] == ["GigaChat-2", "Embeddings"]
    assert result["balance_http_status"] == balance_status
    assert "private" not in json.dumps(result)
    if balance_status == 200:
        assert result["balance"] == [{"model": "GigaChat", "tokens": 1000000}]
    if balance_status == 403:
        assert "не определён" in result["balance"]


def test_missing_credentials_and_ca_do_not_send(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Network access is forbidden")

    monkeypatch.setattr("app.provider_admin.ChatProvider", unexpected)
    assert check_gigachat(settings)["status"] == "key_missing"
    settings.gigachat_credentials = "private-auth"
    assert check_gigachat(settings)["status"] == "ca_missing"


def test_ca_download_failure_preserves_existing_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "gigachat-chain.pem"
    bundle.write_text("previous trusted chain")
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not a certificate"))
    )
    monkeypatch.setattr("app.provider_admin.httpx.Client", lambda **kwargs: client)
    with pytest.raises(ProviderError, match="цепочку"):
        download_ca(tmp_path)
    assert bundle.read_text() == "previous trusted chain"


def test_yandex_configuration_is_explicit_and_does_not_enable_payments(
    db_session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    @contextmanager
    def session_override(url: str):
        yield db_session

    monkeypatch.setattr("app.provider_admin.get_session", session_override)
    settings.yandex_folder_id = "test-folder"
    args = argparse.Namespace(
        qwen_uri="gpt://test-folder/qwen3.6-35b-a3b/latest",
        yandexgpt_uri="gpt://test-folder/yandexgpt-5.1/latest",
        qwen_in=Decimal("0.2"),
        qwen_out=Decimal("0.3"),
        yandexgpt_in=Decimal("0.8"),
        yandexgpt_out=Decimal("0.8"),
    )
    report = configure_yandex(settings, args)
    assert report["generation_calls"] == 0 and not settings.paid_calls_enabled
    configs = list(db_session.scalars(select(ModelConfig).where(ModelConfig.provider == "yandex")))
    assert len(configs) == 2 and all(not model.active for model in configs)
    assert {m.model_uri for m in configs} == {args.qwen_uri, args.yandexgpt_uri}


def test_prompt_versions_preserve_previous_instructions(db_session: Session) -> None:
    from app.models import Prompt
    from app.seed import seed

    seed(db_session)
    original = db_session.scalar(select(Prompt).where(Prompt.version == "v1"))
    before = (original.id, original.text, original.schema)
    seed(db_session, "v2")
    revised = db_session.scalar(select(Prompt).where(Prompt.version == "v2"))
    assert revised.id != original.id and revised.text != original.text
    assert (original.id, original.text, original.schema) == before
    seed(db_session, "v2")
    assert len(list(db_session.scalars(select(Prompt)))) == 2


@pytest.mark.parametrize("encoding", ["DER", "PEM"])
def test_install_official_named_ca_files_and_reject_expired(tmp_path: Path, encoding: str) -> None:
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Russian Trusted Root CA")])
    now = datetime.now(UTC)

    def certificate(name: str, expired: bool = False) -> bytes:
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(root_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2))
            .not_valid_after(now + timedelta(days=-1 if expired else 30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        return cert.public_bytes(getattr(serialization.Encoding, encoding))

    root, sub = tmp_path / "root.cer", tmp_path / "sub.cer"
    root.write_bytes(certificate("Russian Trusted Root CA"))
    sub.write_bytes(certificate("Russian Trusted Sub CA"))
    report = install_ca(tmp_path, [root, sub])
    bundle = tmp_path / "gigachat-chain.pem"
    assert bundle.read_text().count("BEGIN CERTIFICATE") == 2
    assert len(report["certificates"]) == 2
    assert all(len(c["sha256_der"]) == 64 for c in report["certificates"])
    previous = bundle.read_bytes()
    sub.write_bytes(certificate("Russian Trusted Sub CA", expired=True))
    with pytest.raises(ProviderError, match="Неверный CA"):
        install_ca(tmp_path, [root, sub])
    assert bundle.read_bytes() == previous
    with pytest.raises(ProviderError, match="выпускающий"):
        install_ca(tmp_path, [root])
    assert bundle.read_bytes() == previous
