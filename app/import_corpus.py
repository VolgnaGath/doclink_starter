import argparse
import json
from pathlib import Path

from app.config import Settings
from app.db import get_session
from app.services.corpus_import import CorpusError, import_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт корпуса с изолированным эталоном")
    parser.add_argument("--path", type=Path, default=Path("/data/corpus"))
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--tune-share", type=float, default=0.3)
    parser.add_argument("--base-only", action="store_true", help="Только real/synth (корпус v2)")
    args = parser.parse_args()
    settings = Settings()
    try:
        with get_session(settings.database_url) as session:
            report = import_corpus(
                session,
                args.path,
                settings,
                args.seed,
                args.tune_share,
                include_extra=not args.base_only,
            )
    except CorpusError as exc:
        parser.exit(1, f"Импорт отменён: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
