"""Install the explicitly chosen operational method from the stage 4 freeze."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.config import Settings
from app.db import get_session
from app.models import ModelConfig, Prompt
from app.services.embeddings import EncodingConfig
from app.services.extraction import configuration
from app.services.linking.types import LinkingConfig
from app.services.pipeline import install_configuration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.freeze.read_text())
    frozen = report["frozen"]
    with get_session(Settings().database_url) as session:
        model = session.get(ModelConfig, UUID(frozen["model_id"]))
        if model is None or frozen["selected_model"] != "Qwen":
            raise ValueError("В приёмке должна быть выбрана существующая Qwen")
        snapshots = [s for p in frozen["extraction_protocols"].values() for s in p["models"]]
        prompt = next(
            (
                p
                for p in session.scalars(select(Prompt).where(Prompt.name == "extract"))
                if configuration(model, p) in snapshots
            ),
            None,
        )
        if prompt is None:
            raise ValueError("Конфигурация извлечения не совпадает с приёмкой")
        install_configuration(
            session,
            model,
            prompt,
            LinkingConfig.model_validate(frozen["config"]),
            EncodingConfig.model_validate(frozen["encoding"]),
            protocol_hash=report["protocol_hash"],
        )
        session.commit()
    print("Оперативная конфигурация combined + Qwen установлена из замороженной приёмки")


if __name__ == "__main__":
    main()
