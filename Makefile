# Все повторяемые команды проекта. Список целей: make help.
# Цели up/build/migrate/revision/seed/test/lint требуют каркаса этапа 1;
# import-corpus — этапа 2. spec/spec-check/spec-lint работают на host Node 22+.

COMPOSE ?= docker compose
UV_IMAGE ?= ghcr.io/astral-sh/uv:0.5.18-python3.12-bookworm-slim
VERIFY_COMPOSE = docker compose -p doclink-verify -f docker-compose.verify.yml

.DEFAULT_GOAL := help
.PHONY: help spec spec-check spec-lint lock up down logs ps build migrate revision seed import-corpus test test-fast lint format smoke-paid db-shell

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

build: ## Собрать образы без запуска
	$(COMPOSE) build

migrate: ## Применить миграции Alembic
	$(COMPOSE) run --rm api alembic upgrade head

revision: ## Новая миграция: make revision m="описание"
	@test -n "$(m)" || { echo 'usage: make revision m="описание"'; exit 1; }
	$(COMPOSE) build api
	$(COMPOSE) run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR)/migrations:/app/migrations" api alembic revision --autogenerate -m "$(m)"

seed: ## Загрузить конфигурации моделей и промпт v1
	$(COMPOSE) run --rm api python -m app.seed

import-corpus: ## Импортировать корпус: split seed=2026, tune_share=0.3
	$(COMPOSE) run --rm api python -m app.import_corpus --path /data/corpus --seed 2026 --tune-share 0.3

test: ## Тесты без платных вызовов
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q

test-fast: ## Быстрые тесты без медленных маркеров
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q -m "not slow"

lint: ## Проверка стиля без изменений
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api sh -c "ruff check . && ruff format --check ."

format: ## Форматирование и автоисправления
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api sh -c "ruff format . && ruff check --fix . && ruff format ."

smoke-paid: ## Платный smoke: только при PAID_CALLS_ENABLED=true и BUDGET_RUB>0
	@test "$${PAID_CALLS_ENABLED:-false}" = "true" || { echo "PAID_CALLS_ENABLED must be true"; exit 1; }
	@test "$${BUDGET_RUB:-0}" -gt 0 2>/dev/null || { echo "BUDGET_RUB must be > 0"; exit 1; }
	$(COMPOSE) run --rm api python -m app.smoke_paid

db-shell: ## psql в контейнере базы
	$(COMPOSE) exec db psql -U doclink doclink

smoke-ocr: ## Реальный локальный OCR смешанного PDF без внешних API
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_text_extraction.py -k real_ocr_smoke

verify-up: ## Подготовить отдельную PostgreSQL для приёмки этапа 2
	$(VERIFY_COMPOSE) build api
	$(VERIFY_COMPOSE) up -d --wait db
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head

verify-down: ## Удалить только изолированные контейнеры и volumes приёмки
	$(VERIFY_COMPOSE) down -v

verify-test: verify-up ## Интеграционные тесты на отдельной PostgreSQL, включая импорт и веб
	$(MAKE) test COMPOSE='$(VERIFY_COMPOSE)'
