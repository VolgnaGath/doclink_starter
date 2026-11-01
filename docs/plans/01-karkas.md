# План этапа 1 — Каркас

Составлен: 01.11.2026. Обновлён после согласования: 01.11.2026.
Статус: план принят, реализация не начата.
Основание: `docs/TZ.md` (этап 1), `docs/DECISIONS.md`, `IMPLEMENTATION_STATUS.md`, `CONTEXT.md`,
`contract/openapi/openapi.yaml`.

## Принятые решения

- Зависимости — `uv` + `uv.lock`; версия uv закреплена одинаково в Makefile и Dockerfile.
- Один образ `doclink:dev` содержит runtime- и dev-зависимости; его используют `api`, `worker`,
  `mock1c`, миграции и тесты. Multi-stage-сборка не вводится.
- Проект не упаковывается: `[tool.uv] package = false`. Версия хранится в `app.__version__`,
  тест сверяет её с `pyproject.toml`.
- Первая миграция Alembic включает только pgvector; downgrade не удаляет расширение.
- mock1c на этапе 1 — каркас с `/health` и пустым OData-списком. Профиль `ollama` не добавляется.
- `/health` при недоступной БД возвращает 200 `degraded`. Docker healthcheck проверяет liveness:
  HTTP 200 и валидный JSON; `degraded` не делает контейнер нездоровым.
- Наружу публикуются только порты 8000 (`api`) и 8001 (`mock1c`); порт `db` не публикуется.
- `mock1c/inbox` создаётся с `.gitkeep`, но не монтируется до появления коннектора на этапе 7.
- CSRF-токен на этапе 1 не вводится; это фиксируется как открытый вопрос в `CONTEXT.md`,
  без изменения `docs/DECISIONS.md`.

## Граница этапа

**Входит:** Python-проект и lock-файл, FastAPI-приложение, вход по cookie-сессии, публичный `/health`,
Dockerfile с Tesseract (rus), compose `api`/`worker`/`db`/`mock1c`, Alembic с baseline-миграцией pgvector,
минимальный worker и mock1c, тесты, `make lint`/`make test`, README/CONTEXT/STATUS.

**Не входит:** доменные таблицы и модели (этап 2), загрузка/OCR/импорт корпуса (этап 2), `/api/v1`,
провайдеры и `llm_calls` (этап 3), эмбеддинги и связывание (этап 4), обработка `jobs`, seed моделей,
HTMX, полный OData mock1c и монтирование inbox (этап 7), профиль `ollama`.

## Предпосылки

- Docker Desktop запущен; `docker version` и `docker compose version` выполняются успешно.
- Все проверки выполняются в контейнерах. Хостовый Python и uv не требуются.
- `.env` создаётся из `.env.example`; до запуска заменяются `APP_PASSWORD` и `SECRET_KEY`.
  Пустые значения и `change-me` запрещены.
- Если Docker недоступен, проверки не имитируются: причина фиксируется в `CONTEXT.md`.

## Структура файлов

```text
app/
  __init__.py       # __version__
  main.py           # create_app(), middleware, роутеры
  config.py         # Settings (pydantic-settings)
  db.py             # engine, SessionLocal, Base, check_database()
  auth.py           # вход, выход, require_user
  health.py         # GET /health по контракту
  web.py            # защищённая заглушка «Сводка»
  worker.py         # проверка БД и heartbeat без обработки jobs
  templates/        # base.html, login.html, index.html
  static/           # app.css
mock1c/
  __init__.py
  main.py           # /health + пустой OData-список
  inbox/.gitkeep    # без монтирования на этапе 1
migrations/
  env.py, script.py.mako
  versions/0001_baseline_pgvector.py
tests/
  conftest.py, test_health.py, test_auth.py, test_version.py
alembic.ini
Dockerfile
.dockerignore
docker-compose.yml
pyproject.toml
uv.lock
```

В том же этапе обновляются `Makefile`, `.env.example`, `.gitignore`, README, `CONTEXT.md` и
`IMPLEMENTATION_STATUS.md`.

## Шаги реализации

1. **Python-проект и инструменты**
   - `pyproject.toml`: Python `>=3.12,<3.13`, `[tool.uv] package = false`, `name = "doclink"`,
     `version = "0.1.0"`.
   - Runtime-зависимости: FastAPI, `uvicorn[standard]`, SQLAlchemy 2, Alembic, `psycopg[binary]`,
     Jinja2, pydantic-settings, itsdangerous, python-multipart.
   - Dev-группа: pytest, httpx, ruff. Асинхронный pytest не добавляется.
   - `uv.lock` генерируется целью `make lock` в закреплённом образе `ghcr.io/astral-sh/uv:<версия>`
     и коммитится. Команда использует `--user $(id -u):$(id -g)`, чтобы не создавать root-owned файлы.
   - Ruff: `target-version=py312`, `line-length=100`, правила `E,F,I,UP,B`; исключить `reference/`,
     `contract/`, `corpus/`, `data/`.
   - Pytest: `testpaths=["tests"]`, `addopts="-ra"`, маркер `slow`.
   - Makefile: добавить `lock` с описанием для `make help`; для `test`, `test-fast`, `lint`, `format`
     добавить `--no-deps`, чтобы эти команды не поднимали БД. Остальные цели не менять.

2. **Конфигурация и БД**
   - `Settings` объявляет только используемые поля: `DATABASE_URL`, `APP_USER=admin`, `APP_PASSWORD`,
     `SECRET_KEY`, `FILES_DIR=/data/files`, `MOCK1C_URL=http://mock1c:8001`,
     `WORKER_HEARTBEAT_SECONDS=30`; неизвестные переменные игнорируются (`extra="ignore"`).
   - Пустые `APP_PASSWORD`/`SECRET_KEY` и значение `change-me` запрещены всегда. Тесты передают явные
     `Settings(_env_file=None, ...)`. Если `SECRET_KEY` короче 32 символов — предупреждение в лог.
   - Ключи провайдеров, `PAID_CALLS_ENABLED` и `BUDGET_RUB` остаются в `.env.example`, но не входят
     в модель настроек до этапа 3.
   - `.env.example` дополняется `POSTGRES_USER=doclink`, `POSTGRES_PASSWORD=doclink`,
     `POSTGRES_DB=doclink` и `WORKER_HEARTBEAT_SECONDS=30`. `DATABASE_URL` остаётся явным;
     README требует держать его в синхроне с `POSTGRES_*`.
   - `db.py`: ленивый синхронный engine с `pool_pre_ping=True` и `connect_timeout=2`, `SessionLocal`,
     `Base`. `check_database()` выполняет `SELECT 1`, перехватывает исключения, пишет warning и
     возвращает `bool`; недоступная БД не роняет приложение.

3. **Фабрика приложения и веб-каркас**
   - `create_app(settings: Settings | None = None)` сохраняет настройки и функцию проверки БД в
     `app.state`; это позволяет подменять их в тестах без monkeypatch глобальных синглтонов.
   - Роутеры `auth`, `health`, `web`; `/docs` и `/openapi.json` остаются публичными.
   - `SessionMiddleware`: cookie `session`, подпись `SECRET_KEY`, `httponly=True`, `same_site=lax`,
     `https_only=False`, `max_age=7 дней`.
   - `GET /` — защищённая заглушка «Сводка» с текстом «раздел в разработке», без фиктивных метрик.
   - Jinja2-шаблоны `base.html`, `login.html`, `index.html` и `app.css`; HTMX не подключается.
     Пути к шаблонам и статике строятся от расположения модулей, а не от текущего каталога.

4. **Вход**
   - `GET /login` — форма; при активной сессии — редирект на `/`.
   - `POST /login` — сверка логина и пароля через `secrets.compare_digest`. Неверные данные — 401 и
     повторный рендер формы с общим сообщением «Неверный логин или пароль», без создания сессии.
   - При успехе в сессию записывается только имя пользователя; редирект на безопасный относительный
     `next` или `/`. Внешние URL и значения вида `//...` игнорируются.
   - `POST /logout` — очистка сессии и редирект на `/login`.
   - `require_user` для HTML — редирект на `/login?next=...`. Помощник JSON-401 для будущего `/api/v1`
     заранее не создаётся.

5. **`/health`**
   - Публичный `GET /health`; Pydantic-модель ответа строго соответствует `HealthStatus`:
     `{status: "ok"|"degraded", db: bool, version: str}`.
   - `version` берётся из `app.__version__`. Проверка БД — `SELECT 1` на каждый запрос.
   - При недоступной БД — 200 с `status="degraded"`; 500 только при непредвиденной внутренней ошибке.
     Тело 500 — `{"code": "internal_error", "message": "..."}` по `ErrorBody`, без стектрейса.
   - Docker healthcheck через Python и `urllib` проверяет HTTP 200 и валидный JSON, но не требует
     `status="ok"`. Доступность БД проверяется отдельно в приёмке.

6. **Docker и compose**
   - `Dockerfile`: `python:3.12-slim`, системные `tesseract-ocr` и `tesseract-ocr-rus`, uv из
     закреплённого образа, `uv sync --frozen`, непривилегированный пользователь uid 10001,
     `CMD uvicorn app.main:app`.
   - В образ копируются приложение, mock1c, миграции, конфигурация и `tests/`; `/data/files`
     создаётся с владельцем приложения. `PYTHONDONTWRITEBYTECODE=1`.
   - Образ `doclink:dev` собирается только сервисом `api`; `worker` и `mock1c` ссылаются на него через
     `image:`, чтобы `make build` не собирал одно и то же трижды.
   - `db`: `pgvector/pgvector:pg16`, переменные `POSTGRES_*`, healthcheck `pg_isready`, том `pgdata`,
     без публикации порта.
   - `api`: порт 8000, `env_file: .env`, зависимость от здоровой БД, тома `files:/data/files` и
     `./corpus:/data/corpus:ro`.
   - `worker`: тот же образ, `python -m app.worker`, те же тома и зависимость от здоровой БД.
   - `mock1c`: тот же образ, порт 8001, `/health` и пустой OData-список; inbox не монтируется.
   - Для всех сервисов — `restart: unless-stopped`. Для HTTP-healthcheck: `interval: 10s`,
     `timeout: 3s`, `retries: 5`, `start_period: 10s`. Worker запускается без healthcheck.
   - `.dockerignore`: `.git`, `.env*`, `data`, `corpus`, `contract/node_modules`, `tsp-output`,
     `reference/results`, `.venv`, `__pycache__`.
   - `.gitignore`: игнорировать содержимое `mock1c/inbox/`, кроме `.gitkeep`.

7. **Alembic**
   - `alembic.ini` и `migrations/env.py` берут URL из `Settings`, `target_metadata = Base.metadata`;
     пароль в `alembic.ini` не записывается.
   - Baseline `0001`: только `CREATE EXTENSION IF NOT EXISTS vector`; доменные таблицы не создаются.
     Downgrade — no-op, чтобы не удалять расширение, которое могут использовать другие объекты.
   - Миграции применяются отдельной командой `make migrate`, не автоматически при старте контейнера.
   - `make revision` на этапе 1 не запускается: пустой `Base.metadata` создал бы пустую миграцию.

8. **Worker и mock1c без имитации работы**
   - Worker циклически проверяет БД и пишет heartbeat в лог каждые `WORKER_HEARTBEAT_SECONDS`;
     корректно завершается по SIGTERM/SIGINT. Обработки заданий нет до появления таблицы `jobs`.
   - mock1c: `GET /health` → `{"status":"ok"}` и
     `GET /odata/standard.odata/Catalog_Документы` → `{"value": []}`.
     Выдуманные документы и связи не возвращаются.

9. **Тесты**
   - `test_health.py`: публичный доступ, форма ответа, `db=true → ok`, `db=false → degraded` при 200,
     совпадение `version` с `app.__version__`.
   - `test_auth.py`: редирект с `/` на `/login?next=/`; неверный пароль — 401 без cookie; верный пароль
     создаёт cookie `session`; защищённая страница доступна; logout очищает сессию; внешний `next`
     игнорируется; `GET /login` при активной сессии ведёт на `/`.
   - `test_version.py`: `app.__version__` совпадает с версией в `pyproject.toml` (чтение через `tomllib`).
   - Живая PostgreSQL не требуется: `app.state.check_database` подменяется. `.env` всё равно нужен для
     compose, поскольку объявлен в `env_file`.

## Проверка и критерий приёмки

1. `docker version`, затем `cp .env.example .env` и замена локальных `APP_PASSWORD` и `SECRET_KEY`.
2. `docker compose config -q`.
3. `make build`, `make up`, `make ps` — сервисы запущены; `api`, `db`, `mock1c` здоровы, worker работает.
4. `make test` и `make lint`.
5. `curl -fsS http://localhost:8000/health` → `status=ok`, `db=true`.
6. Вход: неверный пароль даёт 401, верный создаёт сессию, `/` открывается, logout работает.
7. `make migrate`; в `make db-shell` проверить `\dx` и наличие `vector`.
8. Перезапуск api восстанавливает здоровье. Остановка db даёт 200 `degraded` в теле `/health`;
   контейнер api при этом остаётся живым — это ожидаемое поведение.
9. `make down`, затем чистая проверка `docker compose down -v && make up && make migrate`.
10. Фактические результаты записать в `IMPLEMENTATION_STATUS.md` и `CONTEXT.md`; незапущенные проверки —
    с причиной, без предположения об успехе.

Критерий этапа: `docker compose up` поднимает сервис, вход работает.

## Коммиты

Каждый коммит должен оставлять репозиторий запускаемым. Перед каждым — `make lint` и `make test`.

1. `build: add project tooling, Docker image, compose (api/db), health and tests`
   — инструменты, lock-файл, Docker/compose только с `api` и `db`, `/health`, Alembic, тесты;
   этот же коммит приводит `docs/plans/01-karkas.md` к согласованной версии.
2. `feat: add cookie login and protected summary page` — вход, выход, `/`, шаблоны и тесты входа.
3. `feat: add worker heartbeat and mock1c skeleton` — модули и добавление сервисов в compose;
   после него проверяется полная топология.
4. `docs: record stage 1 verification results` — README, `CONTEXT.md`, `IMPLEMENTATION_STATUS.md`.

В `docs/DECISIONS.md` изменения не вносятся. Секреты и `.env` не коммитятся.

## Риски и открытые вопросы

- Первая генерация lock-файла и сборка требуют сети; версия uv закрепляется.
- Docker daemon должен быть запущен до проверок; без него результат не считается проверенным.
- Без `.env` compose не стартует; README явно требует скопировать шаблон и заменить секреты.
- `DATABASE_URL` и `POSTGRES_*` дублируют параметры подключения; их синхронность документируется.
- CSRF-токена нет: для локального прототипа риск принят, смягчение — `SameSite=Lax` и POST для logout.
  Открытый вопрос фиксируется в `CONTEXT.md`; решение пересматривается при выносе сервиса наружу.
- `/docs` содержит только реализованный `/health` и расходится с полным TypeSpec-контрактом до появления
  `/api/v1`; drift-тест по прежнему решению не вводится.
- `CREATE EXTENSION` выполняется пользователем Postgres официального образа, который является
  суперпользователем.
