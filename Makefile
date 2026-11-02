# Все повторяемые команды проекта. Список целей: make help.
# Цели up/build/migrate/revision/seed/test/lint требуют каркаса этапа 1;
# import-corpus — этапа 2. spec/spec-check/spec-lint работают на host Node 22+.

COMPOSE ?= docker compose
UV_IMAGE ?= ghcr.io/astral-sh/uv:0.5.18-python3.12-bookworm-slim
VERIFY_COMPOSE = docker compose -p doclink-verify -f docker-compose.verify.yml

.DEFAULT_GOAL := help
.PHONY: help spec spec-check spec-lint lock up down logs ps build migrate revision seed import-corpus test test-fast lint format smoke-paid db-shell
.PHONY: smoke-ocr verify-up verify-down verify-test verify-migrations verify-import
.PHONY: extract freeze-extraction reconcile-call verify-extraction
.PHONY: gigachat-ca gigachat-ca-install check-gigachat configure-yandex
.PHONY: snapshot-corpus audit-corpus import-corpus-original
.PHONY: configure-acceptance
.PHONY: pause-worker resume-worker
.PHONY: audit-extraction
.PHONY: install-addendum audit-addendum import-corpus-base
.PHONY: embedding-download smoke-embedding embed verify-embedding
CERT_DIR ?= $(HOME)/Downloads

help: ## Показать список целей
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

spec: ## Скомпилировать TypeSpec-контракт в OpenAPI
	npm --prefix contract ci
	npm --prefix contract run compile

spec-check: ## Проверить, что OpenAPI-артефакт соответствует TypeSpec
	npm --prefix contract ci
	npm --prefix contract run compile
	@test -z "$$(git status --porcelain -- contract/openapi)" || { \
		echo "OpenAPI artifact is stale: run make spec and commit contract/openapi"; \
		git status --porcelain -- contract/openapi; exit 1; }

spec-lint: ## Проверить форматирование TypeSpec
	npm --prefix contract ci
	npm --prefix contract run check-format

lock: ## Сгенерировать uv.lock закреплённой версией uv
	docker run --rm --entrypoint uv --user "$$(id -u):$$(id -g)" \
		-v "$(CURDIR):/workspace" -w /workspace $(UV_IMAGE) --no-cache lock

up: ## Поднять сервисы: docker compose up -d --build
	$(COMPOSE) up -d --build

down: ## Остановить и удалить контейнеры
	$(COMPOSE) down

logs: ## Смотреть логи всех сервисов
	$(COMPOSE) logs -f

ps: ## Статус контейнеров
	$(COMPOSE) ps

pause-worker: ## Приостановить обычный worker на время управляемой CLI-приёмки
	$(COMPOSE) stop worker

resume-worker: ## Вернуть обычный worker после CLI-приёмки
	$(COMPOSE) up -d worker

build: ## Собрать образы без запуска
	$(COMPOSE) build

migrate: ## Применить миграции Alembic
	$(COMPOSE) run --rm api alembic upgrade head

revision: ## Новая миграция: make revision m="описание"
	@test -n "$(m)" || { echo 'usage: make revision m="описание"'; exit 1; }
	$(COMPOSE) build api
	$(COMPOSE) run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR)/migrations:/app/migrations" api alembic revision --autogenerate -m "$(m)"

seed: ## Загрузить модели и промпт, ARGS='--prompt-version v2' для новой инструкции
	$(COMPOSE) run --rm api python -m app.seed $(ARGS)

gigachat-ca: ## Получить доверенную цепочку НУЦ по HTTPS в certs/ (без отключения TLS)
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -w /workspace api python -m app.provider_admin download-ca

gigachat-ca-install: ## Установить CA из Downloads (PEM/DER): ARGS='--certificate /incoming/имя.cer ...', CERT_DIR=папка
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -v "$(CERT_DIR):/incoming:ro" -w /workspace api python -m app.provider_admin install-ca $(ARGS)

check-gigachat: ## Проверить OAuth, модели и баланс GigaChat без генераций и без вывода ключей
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m app.provider_admin check-gigachat

configure-yandex: ## Сохранить URI/тарифы двух моделей: ARGS='--qwen-uri ... --qwen-in ... --qwen-out ... --yandexgpt-uri ... --yandexgpt-in ... --yandexgpt-out ...'
	$(COMPOSE) run --rm -v "$(CURDIR):/workspace" -w /workspace api python -m app.provider_admin configure-yandex $(ARGS)

configure-acceptance: ## Настроить три облачные модели, тарифный эквивалент Freemium и источник карточки Qwen
	$(COMPOSE) run --rm -v "$(CURDIR):/workspace" -w /workspace api python -m app.provider_admin configure-acceptance

import-corpus: ## Импортировать корпус: split seed=2026, tune_share=0.3
	$(COMPOSE) run --rm api python -m app.import_corpus --path /data/corpus --seed 2026 --tune-share 0.3

import-corpus-base: ## Воспроизвести импорт v2 без synth_extra
	$(COMPOSE) run --rm api python -m app.import_corpus --path /data/corpus --base-only

install-addendum: ## Сохранить и установить согласованное дополнение: SOURCE=/абсолютный/путь.zip
	@test -n "$(SOURCE)"
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -v "$(SOURCE):/incoming/addendum.zip:ro" -w /workspace api python -m app.corpus_addendum install --source /incoming/addendum.zip

audit-addendum: ## Проверить точные правки и хеши дополнения без вызовов моделей
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -w /workspace api python -m app.corpus_addendum audit

snapshot-corpus: ## Сохранить исходный корпус v1 до редакции, без перезаписи существующего архива
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -w /workspace api python -m app.corpus_revision snapshot

audit-corpus: ## Проверить редакцию v2 против исходного архива и сохранить SHA-256/отчёт
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -w /workspace api python -m app.corpus_revision audit --report corpus_history/v2-audit.json

import-corpus-original: ## Импортировать сохранённый исходный корпус отдельной версией с его отчётом
	$(COMPOSE) run --rm -v "$(CURDIR):/workspace" -w /workspace api python -m app.corpus_revision import-original

test: ## Тесты без платных вызовов
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q

test-fast: ## Быстрые тесты без медленных маркеров
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q -m "not slow"

lint: ## Проверка стиля без изменений
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api sh -c "ruff check . && ruff format --check ."

format: ## Форматирование и автоисправления
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api sh -c "ruff format . && ruff check --fix . && ruff format ."

embedding-download: ## Скачать закреплённый BGE-M3 в volume worker (без передачи документов)
	$(COMPOSE) run --rm --no-deps worker python -m app.embedding_smoke --download

smoke-embedding: ## Реальный BGE-M3 CPU smoke без сети; сначала embedding-download
	$(COMPOSE) -f docker-compose.yml -f docker-compose.embedding-smoke.yml run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace worker python -m app.embedding_smoke $(ARGS)

embed: ## Локальная очередь векторов: ARGS='--document UUID --drain' или '--corpus-version UUID --drain'
	$(COMPOSE) run --rm --no-deps worker python -m app.embed_cli $(ARGS)

verify-embedding: verify-up ## Реальная проверка BGE-M3/pgvector/повтора на отдельной DB; сначала embedding-download
	$(VERIFY_COMPOSE) -f docker-compose.embedding-verify.yml run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_embedding_store.py -k real_embedding_cache

smoke-paid: ## Платный smoke: только при PAID_CALLS_ENABLED=true и BUDGET_RUB>0
	@test "$${PAID_CALLS_ENABLED:-false}" = "true" || { echo "PAID_CALLS_ENABLED must be true"; exit 1; }
	@test -n "$${BUDGET_RUB}" || { echo "BUDGET_RUB must be > 0"; exit 1; }
	$(COMPOSE) run --rm -e PAID_CALLS_ENABLED -e BUDGET_RUB api python -m app.smoke_paid $(ARGS)

extract: ## Очередь/продолжение/отчёт: ARGS='--document UUID --drain' (облачные вызовы — smoke-paid)
	$(COMPOSE) run --rm api python -m app.extract_cli $(ARGS)

freeze-extraction: ## Зафиксировать конфигурацию до test: ARGS='--corpus-version UUID'
	$(COMPOSE) run --rm api python -m app.extract_cli --freeze $(ARGS)

audit-extraction: ## Сохранить read-only JSON приёмки: ARGS='--corpus-version UUID --protocol-hash HASH --output docs/reports/report.json'
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -w /workspace api python -m app.extraction_audit $(ARGS)

reconcile-call: ## Сверка неизвестного расхода: ARGS='--reconcile-call UUID --cost-rub N --note ...'
	$(COMPOSE) run --rm api python -m app.extract_cli $(ARGS)

db-shell: ## psql в контейнере базы
	$(COMPOSE) exec db psql -U doclink doclink

smoke-ocr: ## Реальный локальный OCR смешанного PDF без внешних API
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_text_extraction.py -k real_ocr_smoke

verify-up: ## Подготовить отдельную PostgreSQL для приёмки
	$(VERIFY_COMPOSE) build api
	$(VERIFY_COMPOSE) up -d --wait db
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head

verify-down: ## Удалить только изолированные контейнеры и volumes приёмки
	$(VERIFY_COMPOSE) down -v

verify-test: verify-up ## Интеграционные тесты на отдельной PostgreSQL, включая импорт и веб
	$(MAKE) test COMPOSE='$(VERIFY_COMPOSE)'

verify-migrations: verify-up ## Откат/повтор миграций 0004–0002, проверка metadata на изолированной БД
	$(VERIFY_COMPOSE) run --rm api alembic downgrade 0003
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head
	$(VERIFY_COMPOSE) run --rm api alembic downgrade 0002
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head
	$(VERIFY_COMPOSE) run --rm api alembic downgrade 0001
	$(VERIFY_COMPOSE) exec -T db psql -U doclink -d doclink -v ON_ERROR_STOP=1 -c "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head
	$(VERIFY_COMPOSE) run --rm api alembic check

verify-import: verify-up ## Два CLI-импорта исходного корпуса на изолированной БД
	$(MAKE) import-corpus COMPOSE='$(VERIFY_COMPOSE)'
	$(MAKE) import-corpus COMPOSE='$(VERIFY_COMPOSE)'

verify-extraction: verify-up ## Офлайн-приёмка извлечения, очереди и HTTP-транспортов
	$(VERIFY_COMPOSE) run --rm -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_models_extraction.py
