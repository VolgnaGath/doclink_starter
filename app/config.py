import logging
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://doclink:doclink@db:5432/doclink"
    app_user: str = "admin"
    app_password: str
    secret_key: str
    files_dir: str = "/data/files"
    mock1c_url: str = "http://mock1c:8001"
    worker_heartbeat_seconds: int = Field(default=30, gt=0)

    @model_validator(mode="after")
    def validate_secrets(self) -> Self:
        for field_name in ("app_password", "secret_key"):
            value = getattr(self, field_name)
            if not value.strip() or value.strip().lower() == "change-me":
                raise ValueError(f"{field_name.upper()} must be set and must not be 'change-me'")
        if len(self.secret_key) < 32:
            logger.warning("SECRET_KEY is shorter than 32 characters")
        return self
