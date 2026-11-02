"""Evaluation-only ingestion and exact audit of the approved addendum archive."""

import argparse
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.corpus_import import CorpusError, prepare_corpus


def approved_files(archive: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(archive) as zipped:
        files = {
            name.removeprefix("corpus/"): zipped.read(name)
            for name in zipped.namelist()
            if name.startswith("corpus/synth_extra/") and not name.endswith("/")
        }
    if len(files) != 21 or any(".." in Path(name).parts for name in files):
        raise CorpusError("Неожиданный состав дополнения")
    edits = {
        "synth_extra/extra_016/doc_01.txt": (
            "Основание: по договору № 21 от 11.03.2025",
            "Основание: к договору № 21",
        ),
        "synth_extra/extra_008/doc_01.txt": (
            "Договор 3005/25 от «30» мая 2025 г.",
            "Договор 3005/24 от «30» мая 2024 г.",
        ),
    }
    for name, (old, new) in edits.items():
        if files[name].count(old.encode()) != 1:
            raise CorpusError(f"Неожиданный исходный текст: {name}")
        files[name] = files[name].replace(old.encode(), new.encode())
    name = "synth_extra/documents.csv"
    rows = list(csv.DictReader(io.StringIO(files[name].decode("utf-8-sig")), delimiter=";"))
    groups = {
        "extra_013": "extra_011",
        "extra_015": "extra_014",
        "extra_016": "extra_014",
        "extra_018": "extra_017",
        "extra_019": "extra_017",
    }
    for row in rows:
        row["deal_id"] = groups.get(row["deal_id"], row["deal_id"])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    files[name] = stream.getvalue().encode("utf-8-sig")
    return files


def audit(root: Path, archive: Path, settings: Settings) -> dict[str, Any]:
    expected = approved_files(archive)
    actual = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in (root / "synth_extra").rglob("*")
        if p.is_file()
    }
    if expected != actual:
        raise CorpusError("Дополнение отличается от согласованной редакции")
    base = prepare_corpus(root, 2026, 0.3, settings, include_extra=False)
    extended = prepare_corpus(root, 2026, 0.3, settings)
    if (
        tuple(extended.report[k] for k in ("total", "in_scope", "links", "link_sources"))
        != (252, 250, 171, 147)
        or extended.report["warnings"]
    ):
        raise CorpusError("Неверные количества или временные противоречия дополнения")
    splits = {d.key: d.split for d in extended.documents}
    if any(splits[d.key] != d.split for d in base.documents):
        raise CorpusError("Изменилось разбиение основного корпуса")
    return {
        "revision": "addendum-1-2026-11-02",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "base_fingerprint": base.fingerprint,
        "fingerprint": extended.fingerprint,
        "report": extended.report,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(actual.items())},
        "approved_edits": [
            "extra_008 reference matches v2",
            "extra_016 reference without date",
            "deal groups: 011/013, 014/015/016, 017/018/019",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "audit"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--root", type=Path, default=Path("corpus"))
    parser.add_argument(
        "--archive", type=Path, default=Path("corpus_history/addendum_1-original.zip")
    )
    parser.add_argument("--report", type=Path, default=Path("corpus_history/addendum_1-audit.json"))
    args = parser.parse_args()
    if args.action == "install":
        if args.source is None or args.archive.exists() or (args.root / "synth_extra").exists():
            parser.error("Нужен --source; перезапись исходника и набора запрещена")
        files = approved_files(args.source)
        with args.archive.open("xb") as stream:
            stream.write(args.source.read_bytes())
        for name, content in files.items():
            path = args.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    report = audit(args.root, args.archive, Settings())
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
