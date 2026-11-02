"""Real CPU smoke and explicit download of the pinned BGE-M3 snapshot."""

import argparse
import json
import logging
import math
from pathlib import Path

from app.config import Settings
from app.services.embeddings import (
    MODEL_ID,
    MODEL_REVISION,
    BGEEmbedder,
    EncodingConfig,
    cosine,
    download_model,
    prepare_text,
)

TEXTS = (
    "Договор на поставку офисной мебели: столы и кресла для рабочих мест.",
    "Акт приёма-передачи офисных столов и кресел по договору поставки мебели.",
    "Техническое обслуживание грузовых автомобилей и замена моторного масла.",
    "Поставщик передаёт заказчику офисные столы и кресла. " * 80,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download", action="store_true", help="Скачать только закреплённую модель"
    )
    parser.add_argument("--output", type=Path, help="Сохранить JSON без текстов и векторов")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = Settings()
    if args.download:
        path = download_model(settings.embedding_cache_dir)
        print(json.dumps({"model": MODEL_ID, "revision": MODEL_REVISION, "snapshot_path": path}))
        return 0
    config = EncodingConfig(cpu_threads=settings.embedding_cpu_threads)
    embedder = BGEEmbedder(
        settings.embedding_cache_dir,
        config=config,
        local_files_only=True,
        cpu_threads=settings.embedding_cpu_threads,
    )
    results = [embedder.encode(prepare_text(text, config)) for text in TEXTS]
    related = cosine(results[0].vector, results[1].vector)
    unrelated = cosine(results[0].vector, results[2].vector)
    if related <= unrelated:
        raise RuntimeError("Связанный пример не ближе контрольного")
    if results[3].chunks <= 1 or results[3].tokens <= config.chunk_tokens:
        raise RuntimeError("Длинный пример не прошёл несколько окон")
    for result in results:
        if len(result.vector) != 1024 or not math.isclose(
            sum(value * value for value in result.vector), 1, abs_tol=1e-5
        ):
            raise RuntimeError("Нарушена размерность или нормализация")
    # Run this command again to verify model loading from the same persistent volume.
    report = json.dumps(
        {
            "model": MODEL_ID,
            "revision": MODEL_REVISION,
            "snapshot": json.loads(results[0].snapshot),
            "device": "cpu",
            "local_files_only": True,
            "dimensions": 1024,
            "related_cosine": related,
            "unrelated_cosine": unrelated,
            "documents": [
                {
                    "input_hash": r.input_hash,
                    "tokens": r.tokens,
                    "chunks": r.chunks,
                    "truncated": r.truncated,
                    "duration_ms": r.duration_ms,
                }
                for r in results
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
