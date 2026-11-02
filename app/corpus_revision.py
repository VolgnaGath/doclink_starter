"""Evaluation-only provenance/audit for the user-approved synthetic chronology revision.

This utility snapshots and audits data. Corpus text/CSV edits are reviewed separately.
It must not be imported by linking or input preparation.
"""

import argparse
import csv
import hashlib
import io
import json
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import Settings
from app.db import get_session
from app.services.corpus_import import CorpusError, import_corpus, prepare_corpus

CONTRACTS = [
    (
        "synth_010/doc_07",
        "642-ОК/2026",
        "642-ОК/2025",
        "13.02.2026",
        "13.02.2025",
        "13.02.2027",
        "13.02.2026",
    ),
    (
        "synth_012/doc_06",
        "3005/25",
        "3005/24",
        "30.05.2025",
        "30.05.2024",
        "30.05.2026",
        "30.05.2025",
    ),
    ("synth_013/doc_04", "10/25", "10/24", "18.10.2025", "18.10.2024", "18.10.2026", "18.10.2025"),
    ("synth_019/doc_07", "79/25", "79/24", "13.03.2025", "13.03.2024", "13.03.2026", "13.03.2025"),
    (
        "synth_025/doc_09",
        "0712/25",
        "0712/24",
        "07.12.2025",
        "07.12.2024",
        "07.12.2026",
        "07.12.2025",
    ),
]
ACT = "synth_012/doc_03"
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def corpus_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for part in ("real", "synth")
        for path in sorted((root / part).rglob("*"))
        if path.is_file()
    }


def snapshot(root: Path, archive: Path, settings: Settings) -> dict[str, Any]:
    if archive.exists():
        raise CorpusError("Исходный архив уже существует; перезапись запрещена")
    prepared = prepare_corpus(root, 2026, 0.3, settings)
    if len(prepared.report["warnings"]) != 10:
        raise CorpusError("Ожидался исходный корпус с десятью временными противоречиями")
    inputs = corpus_files(root)
    # Stable metadata makes the archive reproducible, irrespective of local mtimes.
    with archive.open("xb") as stream, zipfile.ZipFile(stream, "w") as zipped:
        for name, content in sorted(inputs.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zipped.writestr(info, content)
    return {
        "archive": archive.name,
        "sha256": digest(archive.read_bytes()),
        "fingerprint": prepared.fingerprint,
        "files": len(inputs),
        **prepared.report,
    }


@contextmanager
def archived_corpus(archive: Path) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="doclink-original-") as directory:
        root = Path(directory)
        with zipfile.ZipFile(archive) as zipped:
            for info in zipped.infolist():
                path = PurePosixPath(info.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not path.parts
                    or path.parts[0] not in {"real", "synth"}
                ):
                    raise CorpusError("Некорректный путь в снимке корпуса")
            zipped.extractall(root)
        yield root


def russian_date(value: str) -> str:
    date = datetime.strptime(value, "%d.%m.%Y")
    return f"«{date.day:02d}» {MONTHS[date.month - 1]} {date.year} г."


def audit(root: Path, archive: Path, settings: Settings) -> dict[str, Any]:
    current = prepare_corpus(root, 2026, 0.3, settings, include_extra=False)
    after = corpus_files(root)
    with archived_corpus(archive) as original_root:
        original = prepare_corpus(original_root, 2026, 0.3, settings)
        before = corpus_files(original_root)
    expected_texts = {}
    fields = {ACT: {"date": "05.04.2025"}}
    for doc, old_num, new_num, old_date, new_date, old_end, new_end in CONTRACTS:
        path = f"synth/{doc}.txt"
        content = before[path].decode("utf-8")
        replacements = [
            (f"ДОГОВОР № {old_num}", f"ДОГОВОР № {new_num}"),
            (russian_date(old_date), russian_date(new_date)),
            (f"действует с {old_date} по {old_end}", f"действует с {new_date} по {new_end}"),
        ]
        for old, new in replacements:
            if content.count(old) != 1:
                raise CorpusError(f"Неожиданный исходный текст: {path}")
            content = content.replace(old, new, 1)
        expected_texts[path] = content.encode("utf-8")
        fields[doc] = {"number": new_num, "date": new_date}
    path = f"synth/{ACT}.txt"
    old_header = f"АКТ № 738 от {russian_date('16.03.2025')}"
    new_header = f"АКТ № 738 от {russian_date('05.04.2025')}"
    if before[path].decode("utf-8").count(old_header) != 1:
        raise CorpusError("Неожиданный исходный заголовок акта")
    expected_texts[path] = (
        before[path].decode("utf-8").replace(old_header, new_header, 1).encode("utf-8")
    )
    expected_changed = set(expected_texts) | {"synth/documents.csv"}
    changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    if changed != expected_changed or any(
        after.get(name) != content for name, content in expected_texts.items()
    ):
        raise CorpusError("Изменения файлов отличаются от согласованной редакции")

    def rows(data: bytes) -> list[dict[str, str]]:
        return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))

    expected_rows = rows(before["synth/documents.csv"])
    for row in expected_rows:
        row.update(fields.get(row["doc_id"], {}))
    if rows(after["synth/documents.csv"]) != expected_rows:
        raise CorpusError("Изменились несогласованные поля или порядок строк CSV")
    # Keep fingerprints reproducible through the checkout rules in .gitattributes.
    old_csv, new_csv = before["synth/documents.csv"], after["synth/documents.csv"]
    if (old_csv.count(b"\r\n"), old_csv.count(b"\n")) != (
        new_csv.count(b"\r\n"),
        new_csv.count(b"\n"),
    ):
        raise CorpusError("Изменился формат концов строк CSV; восстановите исходный CRLF")
    for prepared in (original, current):
        if tuple(
            prepared.report[key] for key in ("total", "in_scope", "links", "link_sources")
        ) != (233, 231, 156, 138):
            raise CorpusError("Изменились контрольные количества корпуса")
    if len(original.report["warnings"]) != 10 or current.report["warnings"]:
        raise CorpusError("Временные противоречия эталона не устранены")
    if {d.key: d.split for d in original.documents} != {d.key: d.split for d in current.documents}:
        raise CorpusError("Изменилось разбиение по сделкам")
    if current.fingerprint == original.fingerprint:
        raise CorpusError("Fingerprint версии не изменился")
    return {
        "revision": "v2-chronology-2026-11-01",
        "archive_sha256": digest(archive.read_bytes()),
        "original": {"fingerprint": original.fingerprint, **original.report},
        "current": {"fingerprint": current.fingerprint, **current.report},
        "changed_files": [
            {
                "path": name,
                "before_sha256": digest(before[name]),
                "after_sha256": digest(after[name]),
            }
            for name in sorted(changed)
        ],
        "split_unchanged": True,
        "csv_field_changes": fields,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="История и аудит редакции корпуса, без вызовов моделей"
    )
    parser.add_argument("action", choices=["snapshot", "audit", "import-original"])
    parser.add_argument("--path", type=Path, default=Path("corpus"))
    parser.add_argument("--archive", type=Path, default=Path("corpus_history/v1-original.zip"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    settings = Settings()
    try:
        if args.action == "snapshot":
            report = snapshot(args.path, args.archive, settings)
        elif args.action == "audit":
            report = audit(args.path, args.archive, settings)
        else:
            with (
                archived_corpus(args.archive) as root,
                get_session(settings.database_url) as session,
            ):
                report = import_corpus(session, root, settings)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            args.report.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    except (CorpusError, OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"Корпус: {exc}") from None


if __name__ == "__main__":
    main()
