# План этапа 2 — Документы и корпус

Составлен: 01.11.2026. Статус: план принят, реализация не начата. Основание: `docs/TZ.md` (этап 2), `docs/DECISIONS.md`, `IMPLEMENTATION_STATUS.md`, `CONTEXT.md`, `docs/plans/01-karkas.md`.

## Принятые решения

- Этап 2 вводит доменные таблицы, связанные с документами и корпусом: `clients`, `counterparties`, `corpus_versions`, `documents`, `document_texts`, `anon_maps`. Таблицы `links`, `extractions`, `llm_calls`, `jobs` и прочие — этапы 3–6; их не создаём сейчас.
- Эталонные связи хранятся в `documents.gold` (JSONB), а не в `links`: инвариант «в `links` только подтверждённые/ручные» не нарушается, эталон не попадает во вход движка.
- Загрузка и импорт сохраняют файл по SHA-256 в `/data/files`; дубликаты по хешу не дублируются.
- Текст извлекается синхронно при загрузке/импорте: PDF — pdfplumber + OCR Tesseract (rus) для страниц без текстового слоя; DOCX — python-docx; TXT — UTF-8. Результат в `document_texts`, статус документа `text`.
- Корпус импортируется командой `make import-corpus` (уже объявлена): `python -m app.import_corpus --path /data/corpus --seed 2026 --tune-share 0.3`. Разбиение tune/test — по `deal_id`, seed фиксируется; все документы одной сделки попадают в одну часть.
- Загруженные через интерфейс и mock1c документы получают `split=null`, `in_scope=false`, `source=upload|mock1c`.
- `is_anonymized`: для корпуса `true`, для загрузок по умолчанию `false`.
- Веб-слой этапа 2 — список документов с фильтрами, форма загрузки и карточка документа. REST `/api/v1/documents` и `/corpus/import` из контракта остаются на этап 5/3; на этапе 2 реализуются сервисы и веб-страницы, чтобы не раздувать scope.
- Зависимости `pdfplumber`, `pypdfium2`, `pytesseract`, `python-docx` добавляются в `pyproject.toml` и lock-файл; OCR-программа уже в образе.

## Граница этапа

**Входит:** модели и миграция документов/корпуса, хранение файлов по SHA-256, извлечение текста/OCR, импорт корпуса с эталоном и разбиением, список, загрузка и карточка документа, тесты чистых функций.

**Не входит:** провайдеры, `llm_calls`, обезличивание (этап 3), эмбеддинги и связывание (этап 4), очередь и подтверждение (этап 5), REST `/api/v1` (этап 5), mock1c-коннектор (этап 7).

## Структура файлов

```text
app/
  models.py              # SQLAlchemy-модели документов, клиентов, корпуса
  services/
    storage.py           # сохранение файла по SHA-256
    text_extraction.py   # pdfplumber + OCR, docx, txt
    corpus_import.py     # разбор CSV, split, gold
  documents.py           # роутер /documents (список, загрузка, карточка)
  import_corpus.py       # CLI-модуль для make import-corpus
  templates/
    documents.html
    document_detail.html
    document_upload.html
migrations/versions/0002_documents_and_corpus.py
tests/
  test_text_extraction.py
  test_corpus_split.py
```

## Шаги реализации

1. **Модели и миграция**
   - Добавить модели в `app/models.py` с typed mappings и индексами из ТЗ: `documents(client_id, registered_at)`, `document_texts(document_id PK)`.
   - Сгенерировать миграцию `0002` через `make revision m="documents and corpus"` и проверить автогенерацию; при необходимости дописать enum/constraints вручную.
   - `downgrade` удаляет таблицы этапа 2, но не расширение `vector`.

2. **Хранение файлов**
   - `storage.save_file(data, filename, mime) -> (sha256, relative_path)`; путь `/data/files/<первые 2 символа хеша>/<хеш>`.
   - Дедупликация: при существующем файле возвращать прежний путь.

3. **Извлечение текста**
   - `extract_text(path, mime) -> TextResult(text, pages, chars, ocr_pages)`.
   - PDF: pdfplumber; страница со `< 30` символами → OCR через pypdfium2 + pytesseract `rus`; подсчёт `ocr_pages`.
   - DOCX: python-docx, абзацы через `\n`.
   - TXT: чтение UTF-8.
   - Ошибки извлечения не маскируются: возвращается статус `error` и текст ошибки.

4. **Импорт корпуса**
   - Читать `documents.csv` (разделитель `;`, кодировка UTF-8 с BOM).
   - Создавать/находить `Client` по `client` (real) или `client_inn` (synth); `Counterparty` по `cp_inn`/`cp_name` для synth.
   - Сохранять файл-текст из `deal_XXX/doc_YY.txt` в хранилище, создавать `Document` + `DocumentText`.
   - `gold`: из `links.csv` собрать для каждого source список `{target, type}`.
   - `in_scope`: real — из колонки; synth — `true`.
   - `split`: группировка по `deal_id`, `random.Random(seed)`, `tune_share=0.3`.
   - Создавать `CorpusVersion(name, seed, tune_share, ...)`, привязывать все документы.
   - Возвращать статистику: documents, in_scope, gold_links, tune/test counts.

5. **Веб-интерфейс**
   - `GET /documents` — список с фильтрами клиент/вид/статус/часть корпуса.
   - `POST /documents/upload` — несколько файлов + выбор клиента; извлечение текста; редирект на карточку.
   - `GET /documents/{id}` — карточка: метаданные, текст, страницы/символы/OCR.

6. **Тесты**
   - `test_text_extraction.py`: TXT и DOCX (без БД и без PDF).
   - `test_corpus_split.py`: детерминированность split по seed, все документы сделки в одной части, доля tune ≈ 0.3.
   - Импорт корпуса проверяется вручную через `make import-corpus` и SQL.

## Проверка и критерий приёмки

1. `make lint`, `make test`.
2. `make migrate` на чистой БД; проверить таблицы и индексы.
3. `make import-corpus`; проверить `SELECT count(*) FROM documents WHERE in_scope=true` = 231.
4. `SELECT count(*) FROM (SELECT jsonb_array_length(gold) FROM documents) ...` = 156.
5. Проверить `split`: tune ≈ 30% сделок, test ≈ 70%, сделка не разрывается.
6. Загрузка TXT/PDF через `/documents/upload`, карточка показывает текст и метрики OCR.
7. Фактические результаты записать в `IMPLEMENTATION_STATUS.md` и `CONTEXT.md`.

## Риски

- CSV корпуса с BOM и `;`: парсер должен использовать `utf-8-sig` и `delimiter=";"`.
- Имена клиентов real и INN synth: дедупликация по разным ключам; не объединять real и synth-клиентов.
- Путь к corpus в контейнере `/data/corpus` уже монтируется read-only; при ручной проверке использовать именно его.
- OCR зависит от Tesseract в образе; для сканов нужен `tesseract-ocr-rus` (уже в Dockerfile).
- Миграция с enum: Alembic autogenerate может не создать типы автоматически — проверить вручную.

## Коммиты (ориентир)

1. `feat: add document and corpus database models`
2. `feat: add text extraction and SHA-256 file storage`
3. `feat: add corpus import with gold links and split`
4. `feat: add document list, upload and detail pages`
5. `docs: record stage 2 verification results`
