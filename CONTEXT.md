# CONTEXT — DocLink

Обновлено: 01.11.2026 · Этап: 2 «Документы и корпус» в работе

## Состояние

- Этап 0 «Контракт» завершён: TypeSpec 1.17 в `contract/`, артефакт `contract/openapi/openapi.yaml`.
- Этап 1 завершён: Python 3.12 + `uv.lock`, FastAPI, cookie-вход, `/health`, Docker compose
  `api`/`worker`/`db`/`mock1c`, Alembic baseline pgvector, heartbeat worker, пустой OData mock1c.
- Образ `doclink:dev` содержит runtime/dev-зависимости и Tesseract (rus); запуск — через `make up`,
  миграции — через `make migrate`. `.env` обязателен, `change-me` для пароля и секрета запрещён.
- Добавлены модели этапа 2 и миграция `0002`: документы, клиенты, версии корпуса, тексты, anon_maps,
  stage_timings. Рабочие реквизиты отделены от JSONB gold; run_id появится вместе с прогонами.
- Генерация миграций пересобирает образ и сохраняет результат на хосте. lint/test читают актуальный код.
- Для приёмки создан отдельный compose `docker-compose.verify.yml` без пользовательских volumes и портов.
- TypeSpec — дизайн-артефакт: Pydantic-модели FastAPI пишутся руками, drift-теста нет.
- План этапа 2 согласован: `docs/plans/02-dokumenty-i-korpus.md`; одиночная загрузка, полный gold,
  идемпотентный импорт, `stage_timings` и ручной повтор текста. Множественная загрузка и REST — этап 5.

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
- После уточнения плана этапа 2 устранён недоступный Docker CLI: в пользовательском `~/.zshenv`
  добавлен путь Docker Desktop для новых zsh-сессий. `docker version` и Compose доступны;
  `make lint` проходит (17 файлов), `make test` — 13 passed с тем же предупреждением Starlette.

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
- 10 gold-связей synth_010/012/013/019/025 ведут к договорам из будущего: импорт сохраняет их с отчётом;
  исправленную версию согласовать до замораживания протокола, правило дат не ослаблять.
- JSON-схема `prompts/extract_v1.schema.json` отсутствует, создать на этапе 3.
- `npm audit` в `contract/`: high в `undici` через `@scalar/json-magic` (только dev-toolchain TypeSpec).

## Следующий шаг

Продолжить этап 2: хранение файлов и текст/OCR, импорт, веб-страницы и приёмка. Миграция сгенерирована
через `make revision` на изолированной БД; проверка upgrade/downgrade следует далее.

## Важные пути

`AGENTS.md` · `docs/TZ.md` · `docs/DECISIONS.md` · `IMPLEMENTATION_STATUS.md` · `contract/` · `prompts/` · `corpus/` · `reference/`
