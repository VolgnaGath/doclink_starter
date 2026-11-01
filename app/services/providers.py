"""One complete() is exactly one physical generation request; retries live in the journal."""

import json
import ssl
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import Settings


@dataclass(frozen=True)
class Completion:
    text: str
    raw: str
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None = None


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        uncertain: bool = False,
        raw: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.uncertain = uncertain
        self.raw = raw


class LLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Completion: ...


class ChatProvider:
    def __init__(
        self,
        settings: Settings,
        provider: str,
        model: str,
        params: dict[str, Any],
        client: httpx.Client | None = None,
    ) -> None:
        self.settings, self.provider, self.model, self.params = settings, provider, model, params
        context = ssl.create_default_context()
        if provider == "gigachat" and settings.gigachat_ca_bundle:
            context.load_verify_locations(settings.gigachat_ca_bundle)
        self.client = client or httpx.Client(
            verify=context, timeout=settings.llm_timeout_seconds, follow_redirects=False
        )
        self.token = ""
        self.expires_at = 0.0

    def close(self) -> None:
        self.client.close()

    def _oauth(self) -> str:
        if self.token and self.expires_at > time.time() + 30:
            return self.token
        if not self.settings.gigachat_credentials:
            raise ProviderError("Не задан GIGACHAT_CREDENTIALS")
        try:
            response = self.client.post(
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                headers={
                    "Authorization": f"Basic {self.settings.gigachat_credentials}",
                    "RqUID": str(uuid.uuid4()),
                },
                data={"scope": self.settings.gigachat_scope},
            )
            if response.status_code != 200:
                raise ProviderError(
                    f"OAuth HTTP {response.status_code}",
                    retryable=response.status_code in {429, 500, 502, 503, 504},
                )
            payload = response.json()
            self.token = payload["access_token"]
            self.expires_at = payload["expires_at"] / 1000
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # OAuth is not a generation and cannot consume model tokens.
            raise ProviderError("Ошибка OAuth", retryable=True) from exc
        return self.token

    def inspect_account(self) -> dict[str, Any]:
        """OAuth and account metadata only. Never calls generation or returns credentials."""
        if self.provider != "gigachat":
            raise ProviderError("Диагностика аккаунта поддерживается только для GigaChat")
        headers = {"Authorization": f"Bearer {self._oauth()}"}
        result: dict[str, Any] = {"oauth": "ok"}
        for endpoint in ("models", "balance"):
            try:
                response = self.client.get(
                    self.settings.gigachat_api_url.rstrip("/") + "/" + endpoint, headers=headers
                )
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f"Не удалось получить {endpoint}: {type(exc).__name__}"
                ) from None
            result[endpoint + "_http_status"] = response.status_code
            if endpoint == "balance" and response.status_code == 403:
                result["balance"] = (
                    "Недоступен: возможна оплата pay-as-you-go, остаток не определён"
                )
                continue
            if response.status_code != 200:
                result[endpoint] = "Ошибка доступа; тело ответа не выводится"
                continue
            try:
                payload = response.json()
                if endpoint == "models":
                    result["models"] = [
                        row["id"]
                        for row in payload["data"]
                        if isinstance(row, dict)
                        and isinstance(row.get("id"), str)
                        and row["id"].startswith(("GigaChat", "Embeddings"))
                    ]
                else:
                    result["balance"] = [
                        {"model": row["usage"], "tokens": row["value"]}
                        for row in payload["balance"]
                        if isinstance(row, dict)
                        and isinstance(row.get("usage"), str)
                        and row["usage"].startswith(("GigaChat", "Embeddings"))
                        and type(row.get("value")) in {int, float}
                    ]
            except (ValueError, KeyError, TypeError):
                raise ProviderError(f"Невалидный ответ {endpoint}") from None
        return result

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Completion:
        if self.settings.llm_provider == "mock":
            raise ProviderError("Внешние вызовы отключены: LLM_PROVIDER=mock")
        headers: dict[str, str] = {}
        model = self.model
        if self.provider == "yandex":
            if not self.settings.yandex_api_key or not self.settings.yandex_folder_id:
                raise ProviderError("Не заданы YANDEX_API_KEY / YANDEX_FOLDER_ID")
            url = "https://llm.api.cloud.yandex.net/v1/chat/completions"
            headers = {
                "Authorization": f"Api-Key {self.settings.yandex_api_key}",
                "x-folder-id": self.settings.yandex_folder_id,
            }
            if not model.startswith("gpt://"):
                model = f"gpt://{self.settings.yandex_folder_id}/{model}"
        elif self.provider == "gigachat":
            url = self.settings.gigachat_api_url.rstrip("/") + "/chat/completions"
            headers = {"Authorization": f"Bearer {self._oauth()}"}
        elif self.provider == "ollama":
            url = self.settings.ollama_url.rstrip("/") + "/v1/chat/completions"
        else:
            raise ProviderError("Провайдер не поддерживается")
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            **{
                k: v
                for k, v in self.params.items()
                if k in {"temperature", "max_tokens", "top_p", "seed", "reasoning_effort"}
            },
        }
        if self.params.get("json_mode", False):
            if self.provider == "gigachat":
                generation_schema = {**schema, "required": list(schema.get("properties", {}))}
                body["response_format"] = {
                    "type": "json_schema",
                    "schema": generation_schema,
                    "strict": True,
                }
            else:
                body["response_format"] = {"type": "json_object"}
        try:
            response = self.client.post(url, headers=headers, json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise ProviderError("Не удалось установить соединение", retryable=True) from exc
        except httpx.HTTPError as exc:
            # Read/write failures may occur after the provider accepted a paid request.
            raise ProviderError("Исход запроса неизвестен", uncertain=True) from exc
        if response.status_code == 401 and self.provider == "gigachat":
            self.token = ""
            raise ProviderError("HTTP 401: требуется обновление OAuth", retryable=True)
        if response.status_code >= 400:
            # Do not store arbitrary error bodies: gateways may echo credentials.
            raise ProviderError(
                f"Генерация HTTP {response.status_code}",
                retryable=response.status_code == 429,
                uncertain=response.status_code >= 500,
            )
        raw = response.text
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("content is not text")
            usage = payload.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}

            def token_count(key: str) -> int | None:
                value = usage.get(key)
                return value if type(value) is int and value >= 0 else None

            details = usage.get("prompt_tokens_details") or {}
            cached = details.get("cached_tokens") if isinstance(details, dict) else None
            prompt_tokens = token_count("prompt_tokens")
            if self.provider == "gigachat":
                # Native GigaChat prompt_tokens/total_tokens exclude the free cached part.
                cached = token_count("precached_prompt_tokens")
                if prompt_tokens is not None:
                    prompt_tokens += cached or 0
            if type(cached) is not int or cached < 0:
                cached = None
            return Completion(content, raw, prompt_tokens, token_count("completion_tokens"), cached)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "Невалидный конверт ответа провайдера", uncertain=True, raw=raw
            ) from exc


def make_provider(settings: Settings, snapshot: dict[str, Any]) -> ChatProvider:
    return ChatProvider(settings, snapshot["provider"], snapshot["model_uri"], snapshot["params"])


def response_text(raw: str) -> str:
    return json.loads(raw)["choices"][0]["message"]["content"]
