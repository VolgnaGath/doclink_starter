"""Generate schema documentation, source appendices and hashed delivery index."""

import ast
import hashlib
import json
import os
from pathlib import Path

from app import models  # noqa: F401 — populate metadata
from app.db import Base
from app.services.model_calls import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def schema_markdown() -> str:
    lines = [
        "# Схема БД DocLink",
        "",
        "Сгенерировано из SQLAlchemy metadata (`make delivery-materials`). "
        "Только схема, без строк данных.",
        "",
    ]
    for name, table in sorted(Base.metadata.tables.items()):
        lines.extend(
            [f"## `{name}`", "", "| Поле | Тип | NULL | PK | Ссылка |", "|---|---|---|---|---|"]
        )
        for c in table.columns:
            target = ", ".join(sorted(f.target_fullname for f in c.foreign_keys))
            lines.append(
                f"| `{c.name}` | `{c.type}` | {'да' if c.nullable else 'нет'} | "
                f"{'да' if c.primary_key else ''} | {target} |"
            )
        lines.append("")
        for constraint in sorted(
            table.constraints,
            key=lambda c: (
                type(c).__name__,
                str(c.name or ""),
                str(getattr(c, "sqltext", "")),
                tuple(col.name for col in c.columns),
            ),
        ):
            lines.append(
                f"- `{type(constraint).__name__}`: "
                + (
                    f"`{constraint.sqltext}`"
                    if hasattr(constraint, "sqltext")
                    else ", ".join(f"`{c.name}`" for c in constraint.columns)
                )
            )
        for index in sorted(table.indexes, key=lambda i: i.name):
            lines.append(
                f"- Индекс `{index.name}`: " + ", ".join(str(e) for e in index.expressions)
            )
        lines.append("")
    return "\n".join(lines)


def appendix(title: str, snippets: list[tuple[str, str, str]]) -> str:
    lines = [
        f"# {title}",
        "",
        "Раскладка фрагментов предварительная: внешний макет приложений не предоставлен.",
        "",
    ]
    for filename, function, purpose in snippets:
        path = ROOT / filename
        source = path.read_text()
        node = next(
            n
            for n in ast.parse(source).body
            if isinstance(n, ast.FunctionDef) and n.name == function
        )
        code = "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])
        commit = os.environ["GIT_COMMIT"]
        lines.extend(
            [
                f"## `{function}`",
                "",
                purpose,
                "",
                f"Источник: `{filename}:{node.lineno}–{node.end_lineno}`; версия кода `{commit}`.",
                f"SHA-256 полного файла: `{hashlib.sha256(path.read_bytes()).hexdigest()}`.",
                "",
                "```python",
                code,
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    (ROOT / "docs/DB_SCHEMA.md").write_text(schema_markdown())
    (ROOT / "docs/APPENDIX_G.md").write_text(
        appendix(
            "Приложение Г — установление связей",
            [
                (
                    "app/services/linking/methods.py",
                    "link",
                    "Чистый вход метода, фильтр допустимых кандидатов и полный рейтинг.",
                ),
                (
                    "app/services/linking/decisions.py",
                    "decide",
                    "Документное решение, неоднозначность и две независимые ссылки.",
                ),
            ],
        )
    )
    (ROOT / "docs/APPENDIX_ZH.md").write_text(
        appendix(
            "Приложение Ж — извлечение и подтверждение",
            [
                (
                    "app/services/extraction.py",
                    "prepared_request",
                    "Обезличивание и явное усечение перед внешней моделью.",
                ),
                (
                    "app/services/extraction.py",
                    "save_result",
                    "История извлечений, валидация и рабочая карточка.",
                ),
                (
                    "app/services/review.py",
                    "open_review",
                    "Происхождение и начало человеческой сессии; "
                    "окончательные связи требуют действия.",
                ),
            ],
        )
    )
    files = [
        ROOT / p
        for p in (
            "README.md",
            "docs/EFFECT.md",
            "docs/DELIVERY.md",
            "docs/DB_SCHEMA.md",
            "docs/APPENDIX_G.md",
            "docs/APPENDIX_ZH.md",
        )
    ]
    files += sorted((ROOT / "docs/reports").glob("stage8-*"))
    files += [
        ROOT / "docs/reports" / p
        for p in (
            "stage6-matrix-2026-11-04.json",
            "stage6-matrix-2026-11-04.md",
            "stage6-matrix-2026-11-04.csv",
            "stage6-matrix-2026-11-04.png",
            "stage6-audit-2026-11-04.json",
        )
    ]
    records = [
        {
            "path": str(p.relative_to(ROOT)),
            "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(set(files))
        if p.exists() and p.name != "stage8-manifest-2026-11-04.json"
    ]
    data = {
        "version": "delivery-manifest-v1",
        "files": records,
        "notes": [
            "Переносимый пакет реальных вычислений находится в data/ и не коммитится; "
            "его хеш и состав — в отчётах export/restore.",
            "Метрики и техническая приёмка отдельно от целей качества и замеров человека.",
        ],
    }
    target = ROOT / "docs/reports/stage8-manifest-2026-11-04.json"
    target.write_text(
        json.dumps({**data, "manifest_hash": fingerprint(data)}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print(f"Схема: {len(Base.metadata.tables)} таблиц; манифест: {len(records)} файлов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
