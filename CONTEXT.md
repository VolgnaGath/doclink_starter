# CONTEXT — DocLink

Обновлено: 01.11.2026 · Этап: 1 «Каркас» завершён, следующий — 2 (документы и корпус)

## Состояние

- Этап 0 «Контракт» завершён: TypeSpec 1.17 в `contract/`, артефакт `contract/openapi/openapi.yaml`.
- Этап 1 завершён: Python 3.12 + `uv.lock`, FastAPI, cookie-вход, `/health`, Docker compose
  `api`/`worker`/`db`/`mock1c`, Alembic baseline pgvector, heartbeat worker, пустой OData mock1c.
- Образ `doclink:dev` содержит runtime/dev-зависимости и Tesseract (rus); запуск — через `make up`,
  миграции — через `make migrate`. `.env` обязателен, `change-me` для пароля и секрета запрещён.
- Доменных таблиц, загрузки документов, `/api/v1`, провайдеров и обработки `jobs` ещё нет.
- TypeSpec — дизайн-артефакт: Pydantic-модели FastAPI пишутся руками, drift-теста нет.

## Что проверено (факты, а не намерения)

- `make lock`, `docker compose config -q`, `make build`, `make up`, `make ps` проходят;
  `api`/`db`/`mock1c` здоровы, worker работает.
- `make test`: 13 passed, одно предупреждение Starlette о будущей замене httpx в TestClient.
  `make lint`: ruff check/format проходят. Платных вызовов нет.
- `/health`: `ok/db=true`; после остановки БД — HTTP 200 `degraded/db=false`, api остаётся живым;
  после запуска БД и перезапуска api здоровье восстанавливается.
- Вход проверен HTTP: неверный пароль 401, верный создаёт сессию, `/` открывается, logout работает.
- `make migrate` устанавливает `vector` 0.8.7; чистый сценарий `down -v && make up && make migrate`
  выполнен успешно. Worker по SIGTERM завершается с кодом 0.
- Корпус: 231 документ в оценке (real 16, synth 215) и 156 эталонных связей (real 9, synth 147).
- `reference/linker.py`, `run_models.py`, `anonymize_pdf.py` и `prompts/extract_v1.txt` совпадают
  со стартовым архивом по SHA-256.

## Доменные сущности

Клиент · Документ · Карточка · Договор · Кандидат · Рекомендация · Связь · Прогон · Вызов модели · Действие бухгалтера.

## Инварианты (подробно — `docs/DECISIONS.md`)

- Цель связи — только договор; вид и дата берутся из карточки, эталон не подставляется.
- Кандидат: тот же клиент, дата договора не позже даты документа, без допуска.
- Каждая связь подтверждается бухгалтером; в `links` только `confirmed`/`manual` со ссылкой на `review_actions`.
- Пороги — только на `tune`, метрики — только на `test`; эталон, `split` и `deal_id` изолированы от движка.
- `M = 0` → Precision = 0; охват включает ошибки и документы без договора.
- Ошибка обработки — отдельный исход, не «нет кандидата»; неизвестная стоимость — `null`.
- Секреты только в `.env`; `reference/` не изменяется и не импортируется движком.

## Открытые вопросы / ⚠ сверить

- CSRF-токена нет: локальный прототип использует `SameSite=Lax` и POST logout; пересмотреть при выносе наружу.
- Точные id моделей: Qwen и YandexGPT 5.1 Pro, GigaChat 2 Lite.
- Корневой сертификат НУЦ Минцифры для GigaChat в образе.
- Тарифы провайдеров (`price_in_1k`, `price_out_1k`).
- В `corpus/real/documents.csv` нет `cp_inn`, `amount`, `subject` — метрики извлечения по real частичны.
- JSON-схема `prompts/extract_v1.schema.json` отсутствует, создать на этапе 3.
- `npm audit` в `contract/`: high в `undici` через `@scalar/json-magic` (только dev-toolchain TypeSpec).

## Следующий шаг

Этап 2: доменные модели и миграции, загрузка документов, извлечение текста/OCR, импорт корпуса
с эталоном и разбиением, список и карточка документа. Критерий — 231 документ и 156 эталонных связей.
План этапа 1 с фактически реализованным каркасом — `docs/plans/01-karkas.md`.

## Важные пути

`AGENTS.md` · `docs/TZ.md` · `docs/DECISIONS.md` · `IMPLEMENTATION_STATUS.md` · `contract/` · `prompts/` · `corpus/` · `reference/`
