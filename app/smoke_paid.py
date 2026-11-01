"""The only CLI entry to live cloud scenarios; no synthetic experiment measurements."""

import json
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db import get_session
from app.extract_cli import execute, parser
from app.models import Client, Document, DocumentText, ModelConfig
from app.seed import seed
from app.services.model_calls import ProcessingError
from app.services.storage import save_blob


def main() -> None:
    cli = parser()
    cli.add_argument("--mode", choices=["smoke", "tune", "corpus"], default="smoke")
    args = cli.parse_args()
    settings = Settings()
    if (
        not settings.paid_calls_enabled
        or settings.budget_rub <= 0
        or settings.llm_provider == "mock"
    ):
        raise SystemExit("Нужны LLM_PROVIDER=live, PAID_CALLS_ENABLED=true и BUDGET_RUB>0")
    try:
        if args.mode == "smoke":
            import io

            with get_session(settings.database_url) as session:
                seed(session)
                models = list(
                    session.scalars(
                        select(ModelConfig).where(
                            ModelConfig.name.in_(["Qwen", "YandexGPT 5.1 Pro", "GigaChat 2 Lite"])
                        )
                    )
                )
                if len(models) != 3 or any(
                    m.price_in_1k is None or m.price_out_1k is None for m in models
                ):
                    raise ProcessingError("Настройте три облачные модели и тарифы на /models")
                client = session.scalar(select(Client).where(Client.namespace == "technical-smoke"))
                if client is None:
                    client = Client(
                        name="ООО «Технический клиент»",
                        inn="7707083893",
                        namespace="technical-smoke",
                    )
                    session.add(client)
                    session.flush()
                doc = session.scalar(select(Document).where(Document.client_id == client.id))
                if doc is None:
                    sample = (
                        "Счёт № Т-1 от 01.01.2026\nК договору № Д-1 от 01.12.2025.\n"
                        "ООО «Технический клиент», ИНН 7707083893.\nИтого 1200 рублей."
                    )
                    blob = save_blob(
                        io.BytesIO(sample.encode()),
                        Path(settings.files_dir),
                        settings.max_file_bytes,
                    )
                    doc = Document(
                        client_id=client.id,
                        source="upload",
                        filename="technical-smoke.txt",
                        sha256=blob.sha256,
                        storage_path=str(blob.path),
                        mime="text/plain",
                        status="text",
                    )
                    session.add(doc)
                    session.flush()
                    session.add(
                        DocumentText(
                            document_id=doc.id, text=sample, pages=1, chars=len(sample), ocr_pages=0
                        )
                    )
                session.commit()
                args.document = doc.id
                args.model = [m.name for m in models]
        elif not args.corpus_version:
            raise ProcessingError("Выберите --corpus-version")
        if args.mode == "tune":
            args.split = "tune"
        args.drain = True
        report = execute(args, settings, paid_entry=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if any(value["all"]["success"] == 0 for value in report.get("by_model", {}).values()):
            raise SystemExit("Есть модель без успешных результатов: проверьте подключение и журнал")
        all_results = report.get("all", {})
        if all_results.get("blocking_errors") or all_results.get("success", 0) + all_results.get(
            "errors", 0
        ) != all_results.get("expected_pairs", 0):
            raise SystemExit(
                "Приёмка заблокирована: незавершённая обработка или ошибки конвейера/бюджета"
            )
    except ProcessingError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
