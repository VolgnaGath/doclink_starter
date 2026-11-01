# Все повторяемые команды проекта. Список целей: make help.
# Цели up/build/migrate/revision/seed/import-corpus требуют каркаса этапа 0–1.

COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help up down logs ps build migrate revision seed import-corpus test test-fast lint format smoke-paid db-shell

help: ## Показать список целей
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

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
	$(COMPOSE) run --rm api alembic revision --autogenerate -m "$(m)"

seed: ## Загрузить конфигурации моделей и промпт v1
	$(COMPOSE) run --rm api python -m app.seed

import-corpus: ## Импортировать корпус: split seed=2026, tune_share=0.3
	$(COMPOSE) run --rm api python -m app.import_corpus --path /data/corpus --seed 2026 --tune-share 0.3

test: ## Тесты без платных вызовов
	$(COMPOSE) run --rm api python -m pytest -q

test-fast: ## Быстрые тесты без медленных маркеров
	$(COMPOSE) run --rm api python -m pytest -q -m "not slow"

lint: ## Проверка стиля без изменений
	$(COMPOSE) run --rm api sh -c "ruff check . && ruff format --check ."

format: ## Форматирование и автоисправления
	$(COMPOSE) run --rm api sh -c "ruff check --fix . && ruff format ."

smoke-paid: ## Платный smoke: только при PAID_CALLS_ENABLED=true и BUDGET_RUB>0
	@test "$${PAID_CALLS_ENABLED:-false}" = "true" || { echo "PAID_CALLS_ENABLED must be true"; exit 1; }
	@test "$${BUDGET_RUB:-0}" -gt 0 2>/dev/null || { echo "BUDGET_RUB must be > 0"; exit 1; }
	$(COMPOSE) run --rm api python -m app.smoke_paid

db-shell: ## psql в контейнере базы
	$(COMPOSE) exec db psql -U doclink doclink
