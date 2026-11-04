"""Offline corpus extraction preflight/transfer and linking card manifests."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from app.config import Settings
from app.db import get_session
from app.services.extraction_transfer import card_snapshot, preflight, transfer
from app.services.model_calls import ProcessingError


def main() -> None:
    parser = argparse.ArgumentParser(description="Перенос извлечений без сетевых вызовов")
    parser.add_argument("action", choices=["preflight", "apply", "cards"])
    parser.add_argument("--source-version", type=UUID)
    parser.add_argument("--corpus-version", type=UUID)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    try:
        with get_session(settings.database_url) as session:
            if args.action == "preflight":
                if not args.source_version or not args.corpus_version:
                    raise ProcessingError("Нужны --source-version и --corpus-version")
                report = preflight(session, args.source_version, args.corpus_version, settings)
            elif args.action == "apply":
                if args.input is None or args.input == args.output:
                    raise ProcessingError("Нужны разные --input (preflight) и --output")
                report = transfer(session, json.loads(args.input.read_text()))
            else:
                if not args.corpus_version:
                    raise ProcessingError("Нужна --corpus-version")
                report = card_snapshot(session, args.corpus_version)
            # A failed file write must not leave an unreported transfer.
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            session.commit()
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "counts": report.get(
                        "counts", {"cards": len(report["cards"])} if "cards" in report else report
                    ),
                    "blockers": report.get("blockers", []),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except ProcessingError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
