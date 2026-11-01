"""Idempotent initial configuration. Never overwrites an existing user's settings."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import ModelConfig, Prompt

ROOT = Path(__file__).resolve().parent.parent


def seed(session: Session, prompt_version: str = "v1") -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(310003)"))
    if prompt_version not in {"v1", "v2"}:
        raise ValueError("Неизвестная версия инструкции")
    if not session.scalar(
        select(Prompt).where(Prompt.name == "extract", Prompt.version == prompt_version)
    ):
        session.add(
            Prompt(
                name="extract",
                version=prompt_version,
                text=(ROOT / f"prompts/extract_{prompt_version}.txt").read_text(),
                schema=json.loads(
                    (ROOT / f"prompts/extract_{prompt_version}.schema.json").read_text()
                ),
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
    parser = argparse.ArgumentParser(
        description="Идемпотентные начальные модели и версия инструкции"
    )
    parser.add_argument("--prompt-version", choices=["v1", "v2"], default="v1")
    args = parser.parse_args()
    with get_session(Settings().database_url) as session:
        seed(session, args.prompt_version)
        session.commit()


if __name__ == "__main__":
    main()
