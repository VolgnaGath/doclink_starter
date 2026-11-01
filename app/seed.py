"""Idempotent initial configuration. Never overwrites an existing user's settings."""

import json
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import ModelConfig, Prompt

ROOT = Path(__file__).resolve().parent.parent


def seed(session: Session) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(310003)"))
    if not session.scalar(select(Prompt).where(Prompt.name == "extract", Prompt.version == "v1")):
        session.add(
            Prompt(
                name="extract",
                version="v1",
                text=(ROOT / "prompts/extract_v1.txt").read_text(),
                schema=json.loads((ROOT / "prompts/extract_v1.schema.json").read_text()),
            )
        )
    configs = [
        ("Regex", "regex", "regex-v1", "local", True),
        ("Qwen", "yandex", "qwen3.6-35b-a3b/latest", "open-weights-cloud", False),
        ("YandexGPT 5.1 Pro", "yandex", "yandexgpt-5.1/latest", "cloud", False),
        ("GigaChat 2 Lite", "gigachat", "GigaChat-2", "cloud", False),
        ("Ollama", "ollama", "gigachat", "local", False),
    ]
    for name, provider, uri, placement, active in configs:
        if not session.scalar(select(ModelConfig).where(ModelConfig.name == name)):
            session.add(
                ModelConfig(
                    name=name,
                    provider=provider,
                    model_uri=uri,
                    placement=placement,
                    active=active,
                    default_card=active,
                    params={"temperature": 0, "max_tokens": 2000},
                )
            )
    session.flush()


def main() -> None:
    with get_session(Settings().database_url) as session:
        seed(session)
        session.commit()


if __name__ == "__main__":
    main()
