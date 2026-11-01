# План этапа 1 — Каркас

Составлен: 01.11.2026. Статус: план принят, реализация не начата.
Основание: `docs/TZ.md` (этап 1), `docs/DECISIONS.md`, `IMPLEMENTATION_STATUS.md`, `CONTEXT.md`,
`contract/openapi/openapi.yaml`.

Принятые решения: зависимости — `uv` + `uv.lock`; в образе этапа 1 только минимальный набор;
первая миграция Alembic включает только pgvector; mock1c на этапе 1 — каркас с `/health`
и пустым OData-списком.

## Граница этапа

**Входит:** Python-проект и lock-файл, FastAPI-приложение, вход по cookie-сессии, публичный `/health`,
Dockerfile с Tesseract (rus), compose `api`/`worker`/`db`/`mock1c`, Alembic с baseline-миграцией pgvector,
минимальный worker и mock1c, тесты, `make lint`/`make test`, README/CONTEXT/STATUS.

**Не входит:** доменные таблицы и модели (этап 2), загрузка/OCR/импорт корпуса (этап 2), `/api/v1`,
провайдеры и `llm_calls` (этап 3), эмбеддинги и связывание (этап 4), обработка `jobs`, seed моделей,
полный OData mock1c (этап 7).

## Структура файлов

```text
app/
  __init__.py
  main.py          # create_app(), middleware, роутеры
  config.py        # Settings (pydantic-settings)
  db.py            # engine, SessionLocal, Base, check_database()
  auth.py          # проверка APP_USER/APP_PASSWORD, require_user, login/logout
  health.py        # GET /health по контракту
  web.py           # защищённая заглушка «Сводка»
  worker.py        # цикл worker: проверка БД + heartbeat, без фейковой обработки
  templates/       # base.html, login.html, index.html
  static/          # app.css
mock1c/
  __init__.py
  main.py          # /health + пустой OData-список
  inbox/.gitkeep
migrations/
  env.py, script.py.mako
  versions/0001_baseline_pgvector.py
tests/
  conftest.py, test_health.py, test_auth.py
alembic.ini
Dockerfile
.dockerignore
docker-compose.yml
pyproject.toml
uv.lock
```

## Шаги реализации

1. **Python-проект и инструменты**
   - `pyproject.toml`: Python 3.12, зависимости каркаса (FastAPI, uvicorn, SQLAlchemy 2, Alembic,
     psycopg 3, Jinja2, pydantic-settings, python-multipart, itsdangerous), dev-группа (pytest, httpx, ruff).
   - `uv.lock` генерируется через uv и коммитится.
   - Настройка ruff: `target-version=py312`, `line-length=100`, исключить `reference/`, `contract/`,
     `corpus/`, `data/`.
   - Настройка pytest: `testpaths=["tests"]`, маркер `slow` для будущих тестов.

2. **Конфигурация и БД**
   - `Settings` читает `.env` и переменные окружения: `DATABASE_URL`, `APP_USER`, `APP_PASSWORD`,
     `SECRET_KEY`, `FILES_DIR`, `MOCK1C_URL`.
   - Валидация при старте: пустые `APP_PASSWORD`/`SECRET_KEY` не допускаются в рабочем режиме.
   - `db.py`: синхронный engine с `pool_pre_ping=True` и `connect_timeout=2`, `SessionLocal`, `Base`;
     `check_database()` выполняет `SELECT 1` и не роняет приложение.

3. **Вход и веб-каркас**
   - `SessionMiddleware` с cookie `session` (имя совпадает с security scheme контракта), подпись
     `SECRET_KEY`, `same_site=lax`, `https_only=False` для локального прототипа.
   - `GET /login` — форма; `POST /login` — сверка через `secrets.compare_digest`, при успехе запись
     пользователя в сессию и редирект на безопасный локальный `next`; неверные данные — 401 и сообщение.
   - `POST /logout` — очистка сессии.
   - `require_user`: для HTML — редирект на `/login`; отдельный помощник для будущего JSON-401 в `/api/v1`.
   - `GET /` — защищённая заглушка «Сводка» без фиктивных метрик. `/docs` остаётся публичным по ТЗ.

4. **`/health`**
   - Публичный, без авторизации; ответ строго по контракту:
     `{status: "ok"|"degraded", db: bool, version: str}`.
   - При недоступной БД — 200 с `status="degraded"`; 500 только при непредвиденной внутренней ошибке.
   - Docker healthcheck проверяет не только HTTP-код, но и `status=="ok"`.

5. **Docker и compose**
   - `Dockerfile`: `python:3.12-slim`, системные `tesseract-ocr` и `tesseract-ocr-rus`, установка
     через uv с `--frozen`, непривилегированный пользователь, `CMD uvicorn app.main:app`.
   - `db`: образ `pgvector/pgvector:pg16`, healthcheck `pg_isready`, именованный том `pgdata`.
   - `api`: порт 8000, `env_file: .env`, зависимость от здоровой БД, тома `/data/files` и
     `./corpus:/data/corpus:ro`.
   - `worker`: тот же образ, `python -m app.worker`, те же тома и зависимости.
   - `mock1c`: тот же образ или отдельная команда, порт 8001, `/health` и
     `GET /odata/standard.odata/Catalog_Документы` → `{"value": []}`; inbox смонтирован, но логика — этап 7.
   - Необязательный профиль `ollama` без влияния на базовый запуск.
   - `.dockerignore` и дополнение `.gitignore` для `mock1c/inbox/*`.

6. **Alembic**
   - `alembic.ini` + `migrations/env.py` берут URL из `Settings`, `target_metadata = Base.metadata`.
   - Baseline-миграция: `CREATE EXTENSION IF NOT EXISTS vector`; доменные таблицы не создаются.
   - Проверка существующих целей `make migrate` и `make revision` без изменений Makefile.

7. **Worker и mock1c без имитации работы**
   - Worker при старте и циклически проверяет БД, пишет heartbeat в лог, корректно завершается по
     SIGTERM/SIGINT. Обработки заданий нет до появления таблицы `jobs`.
   - mock1c не имитирует связи и не возвращает выдуманные документы: только liveness и пустой список.

8. **Тесты**
   - `test_health.py`: публичный доступ, форма ответа, `db=true → ok`, `db=false → degraded`.
   - `test_auth.py`: редирект с `/` на `/login`; неверный пароль не создаёт сессию; верный пароль
     создаёт cookie `session`; защищённая страница доступна; logout очищает сессию; внешний `next`
     игнорируется.
   - Тесты не требуют живой PostgreSQL: проверка БД подменяется через `app.state`.

## Проверка и критерий приёмки

Локально Docker CLI есть в `~/.docker/bin`, но не в `PATH`; проверки выполнять с
`PATH="$HOME/.docker/bin:$PATH"`.

1. `cp .env.example .env`, задать локальные `APP_PASSWORD` и `SECRET_KEY`.
2. `docker compose config -q`.
3. `make build`, `make up`, `make ps` — все сервисы запущены, api/db/mock1c здоровы.
4. `curl -fsS http://localhost:8000/health` → `status=ok`, `db=true`.
5. Проверить вход: неверный пароль отклоняется, верный создаёт сессию, `/` открывается, logout работает.
6. `make migrate`; в psql проверить `\dx` и наличие `vector`.
7. `make test` и `make lint`.
8. Перезапуск api восстанавливает здоровье; остановка db даёт `degraded`.
9. `make down`, затем чистая проверка `docker compose down -v && make up && make migrate`.

Критерий этапа: `docker compose up` поднимает сервис, вход работает.

## Документация и коммиты

- README: актуальный запуск, `.env`, адреса `/`/`/health`/mock1c, миграции, остановка.
- `CONTEXT.md` и `IMPLEMENTATION_STATUS.md`: этап 1 → «сделано», перечислить реально выполненные
  проверки; следующий шаг — этап 2.
- Коммиты небольшими смысловыми блоками с push в `origin/main`: инструменты → приложение и вход →
  Docker/compose/Alembic → тесты → документация. Секреты и `.env` не коммитятся.

## Риски и открытые вопросы

- `uv.lock` и версия uv требуют сети при первой сборке; версию uv зафиксирую в Dockerfile.
- `/health` при падении БД возвращает 200 `degraded`, поэтому healthcheck обязан разбирать тело ответа,
  иначе контейнер будет ложно здоров.
- Без `.env` compose не стартует; README явно требует скопировать шаблон.
- Дрейф `/docs` с TypeSpec-контрактом пока ожидаем: на этапе 1 реализован только `/health`, остальные
  операции появятся на следующих этапах.
