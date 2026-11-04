import logging
from decimal import Decimal
from typing import Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://doclink:doclink@db:5432/doclink"
    app_user: str = "admin"
    app_password: str
    secret_key: str
    files_dir: str = "/data/files"
    max_file_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default=100, gt=0)
    ocr_dpi: int = Field(default=200, ge=72, le=300)
    max_render_pixels: int = Field(default=20_000_000, gt=0)
    ocr_timeout_seconds: int = Field(default=30, gt=0)
    max_docx_uncompressed_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    mock1c_url: str = "http://mock1c:8001"
    mock1c_enabled: bool = True
    mock1c_poll_seconds: int = Field(default=30, ge=1)
    mock1c_timeout_seconds: int = Field(default=10, gt=0)
    worker_heartbeat_seconds: int = Field(default=30, gt=0)
    embedding_cache_dir: str = "/data/models"
    embedding_local_files_only: bool = True
    embedding_cpu_threads: int = Field(default=2, ge=1, le=32)
    llm_provider: str = "mock"
    paid_calls_enabled: bool = False
    budget_rub: Decimal = Field(default=Decimal("0"), ge=0)
    yandex_budget_rub: Decimal | None = Field(default=None, ge=0)
    gigachat_budget_rub: Decimal | None = Field(default=None, ge=0)
    yandex_api_key: str = Field(
        default="", repr=False, validation_alias=AliasChoices("yandex_api_key", "YC_API_KEY")
    )
    yandex_folder_id: str = Field(
        default="", validation_alias=AliasChoices("yandex_folder_id", "YC_FOLDER_ID")
    )
    gigachat_credentials: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices("gigachat_credentials", "GIGACHAT_AUTH_KEY"),
    )
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_api_url: str = "https://api.giga.chat/v1"
    gigachat_ca_bundle: str = ""
    ollama_url: str = "http://ollama:11434"
    llm_timeout_seconds: int = Field(default=120, gt=0)

    @model_validator(mode="after")
    def validate_secrets(self) -> Self:
        for field_name in ("app_password", "secret_key"):
            value = getattr(self, field_name)
            if not value.strip() or value.strip().lower() == "change-me":
                raise ValueError(f"{field_name.upper()} must be set and must not be 'change-me'")
        if len(self.secret_key) < 32:
            logger.warning("SECRET_KEY is shorter than 32 characters")
        return self
