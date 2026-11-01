"""Explicit provider setup and metadata checks. No document or generation endpoints."""

import argparse
import hashlib
import json
import ssl
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.config import Settings
from app.db import get_session
from app.models import ModelConfig
from app.seed import seed
from app.services.providers import ChatProvider, ProviderError


def validate_ca(data: bytes, source: str) -> tuple[str, dict[str, Any]]:
    pem = (
        data.decode("ascii").strip() + "\n"
        if data.lstrip().startswith(b"-----BEGIN")
        else ssl.DER_cert_to_PEM_cert(data)
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=pem)
    certs = context.get_ca_certs()
    if len(certs) != 1:
        raise ValueError("Ожидался один CA-сертификат")
    cert = certs[0]
    names = [
        value for component in cert["subject"] for key, value in component if key == "commonName"
    ]
    allowed = {"Russian Trusted Root CA", "Russian Trusted Sub CA", "Russian Trusted Sub CA 2024"}
    if (
        len(names) != 1
        or names[0] not in allowed
        or not (
            ssl.cert_time_to_seconds(cert["notBefore"])
            <= time.time()
            < ssl.cert_time_to_seconds(cert["notAfter"])
        )
    ):
        raise ValueError("Неверное имя или срок CA-сертификата")
    return pem, {
        "source": source,
        "common_name": names[0],
        "sha256_der": hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest(),
        "expires": cert["notAfter"],
    }


def save_ca(
    directory: Path, certificates: list[str], records: list[dict[str, Any]]
) -> dict[str, Any]:
    names = {record["common_name"] for record in records}
    if "Russian Trusted Root CA" not in names or not any("Sub CA" in name for name in names):
        raise ProviderError("Нужны корневой и хотя бы один выпускающий сертификат НУЦ")
    if not directory.is_dir():
        raise ProviderError("Каталог certs не найден")
    bundle = directory / "gigachat-chain.pem"
    temporary = directory / "gigachat-chain.pem.tmp"
    temporary.write_text("".join(certificates), encoding="ascii")
    temporary.replace(bundle)
    (directory / "gigachat-chain.source.json").write_text(json.dumps(records, indent=2))
    return {"bundle": str(bundle), "certificates": records}


def install_ca(directory: Path, sources: list[Path]) -> dict[str, Any]:
    certificates, records = [], []
    for source in sources:
        try:
            pem, record = validate_ca(source.read_bytes(), source.name)
        except (ValueError, UnicodeError, ssl.SSLError):
            raise ProviderError(f"Неверный CA-сертификат: {source.name}") from None
        certificates.append(pem)
        records.append(record)
    return save_ca(directory, certificates, records)


def download_ca(directory: Path) -> dict[str, Any]:
    certificates = []
    records = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for name in ("russian_trusted_root_ca_pem.crt", "russian_trusted_sub_ca_pem.crt"):
            url = "https://gu-st.ru/content/lending/" + name
            try:
                reply = client.get(url)
                reply.raise_for_status()
                pem, record = validate_ca(reply.content, url)
                certificates.append(pem)
                records.append(record)
            except (httpx.HTTPError, ValueError, ssl.SSLError) as exc:
                raise ProviderError(
                    f"Не удалось получить проверенную цепочку НУЦ: {type(exc).__name__}"
                ) from None
    return save_ca(directory, certificates, records)


def check_gigachat(settings: Settings) -> dict[str, Any]:
    if not settings.gigachat_credentials.strip():
        return {"status": "key_missing", "message": "GIGACHAT_AUTH_KEY пуст; сохраните .env"}
    if not settings.gigachat_ca_bundle or not Path(settings.gigachat_ca_bundle).is_file():
        return {"status": "ca_missing", "message": "Нужна доверенная цепочка GIGACHAT_CA_BUNDLE"}
    adapter = ChatProvider(settings, "gigachat", "", {})
    try:
        result = adapter.inspect_account()
        result["status"] = "ok" if result["models_http_status"] == 200 else "access_error"
        return result
    finally:
        adapter.close()


def configure_yandex(settings: Settings, args: argparse.Namespace) -> dict[str, Any]:
    folder = settings.yandex_folder_id
    if not folder:
        raise ProviderError("Не задан YC_FOLDER_ID")
    configs = [
        ("Qwen", args.qwen_uri, args.qwen_in, args.qwen_out),
        ("YandexGPT 5.1 Pro", args.yandexgpt_uri, args.yandexgpt_in, args.yandexgpt_out),
    ]
    for _, uri, incoming, outgoing in configs:
        if (
            not uri
            or not uri.startswith(f"gpt://{folder}/")
            or any(p is None or not p.is_finite() or p < 0 for p in (incoming, outgoing))
        ):
            raise ProviderError("Укажите URI выбранного каталога и два тарифа для каждой модели")
    with get_session(settings.database_url) as session:
        seed(session)
        result = []
        for name, uri, incoming, outgoing in configs:
            model = session.scalar(
                select(ModelConfig).where(ModelConfig.name == name).with_for_update()
            )
            model.model_uri, model.price_in_1k, model.price_out_1k = uri, incoming, outgoing
            result.append(
                {
                    "name": name,
                    "model_uri": uri,
                    "price_in_1k": str(incoming),
                    "price_out_1k": str(outgoing),
                    "active": model.active,
                }
            )
        session.commit()
    return {"configured": result, "generation_calls": 0}


def configure_acceptance(settings: Settings) -> dict[str, Any]:
    """Apply the agreed providers and published reference rates before any test results."""
    with get_session(settings.database_url) as session:
        seed(session)
        result = []
        for model in session.scalars(select(ModelConfig).with_for_update()):
            if model.name not in {"Qwen", "YandexGPT 5.1 Pro", "GigaChat 2 Lite"}:
                model.active, model.default_card = False, False
                continue
            model.active, model.default_card = True, model.name == "Qwen"
            params: dict[str, Any] = {"temperature": 0, "max_tokens": 2000}
            if model.provider == "gigachat":
                model.price_in_1k = model.price_out_1k = Decimal("0.065")
                params["cost_basis"] = "reference_tariff_freemium"
            else:
                if model.price_in_1k is None or model.price_out_1k is None:
                    raise ProviderError("Сначала задайте согласованные тарифы Yandex")
                params["json_mode"] = True
                params["price_cached_in_1k"] = "0.05" if model.name == "Qwen" else "0.8"
                if model.name == "Qwen":
                    params["reasoning_effort"] = "none"
            model.params = params
            result.append(
                {
                    "name": model.name,
                    "model_uri": model.model_uri,
                    "params": params,
                    "price_in_1k": str(model.price_in_1k),
                    "price_out_1k": str(model.price_out_1k),
                }
            )
        if len(result) != 3:
            raise ProviderError("Не найдены три конфигурации моделей")
        session.commit()
        return {
            "models": result,
            "generation_calls": 0,
            "cash_debit_rub": None,
            "default_card_source": "Qwen",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Настройка и диагностика без генераций")
    parser.add_argument(
        "action",
        choices=[
            "download-ca",
            "install-ca",
            "check-gigachat",
            "configure-yandex",
            "configure-acceptance",
        ],
    )
    parser.add_argument("--certs-dir", type=Path, default=Path("certs"))
    parser.add_argument("--certificate", type=Path, action="append", default=[])
    parser.add_argument("--qwen-uri")
    parser.add_argument("--yandexgpt-uri")
    for field in ("qwen-in", "qwen-out", "yandexgpt-in", "yandexgpt-out"):
        parser.add_argument("--" + field, type=Decimal)
    args = parser.parse_args()
    try:
        if args.action == "download-ca":
            result = download_ca(args.certs_dir)
        elif args.action == "install-ca":
            result = install_ca(args.certs_dir, args.certificate)
        elif args.action == "check-gigachat":
            result = check_gigachat(Settings())
        elif args.action == "configure-acceptance":
            result = configure_acceptance(Settings())
        else:
            result = configure_yandex(Settings(), args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") not in {None, "ok"}:
            raise SystemExit(1)
    except ProviderError as exc:
        raise SystemExit(str(exc)) from None
    except (OSError, ssl.SSLError):
        raise SystemExit("Ошибка локального файла или цепочки TLS") from None


if __name__ == "__main__":
    main()
