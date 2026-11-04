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
.PHONY: link verify-linking
.PHONY: transfer-extraction verify-transfer
.PHONY: accept-linking verify-linking-acceptance
.PHONY: verify-review
.PHONY: configure-pipeline verify-pipeline
.PHONY: accept-upload-start accept-upload-stop
.PHONY: verify-api spec-format
.PHONY: accept-working-set api-schema
CERT_DIR ?= $(HOME)/Downloads

help: ## Показать список целей
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

spec: ## Скомпилировать TypeSpec-контракт в OpenAPI
	npm --prefix contract ci
	npm --prefix contract run compile

spec-check: ## Проверить, что OpenAPI-артефакт соответствует TypeSpec
	npm --prefix contract ci
	node contract/check-artifact.mjs

spec-lint: ## Проверить форматирование TypeSpec
	npm --prefix contract ci
	npm --prefix contract run check-format

spec-format: ## Отформатировать исходники TypeSpec
	npm --prefix contract ci
	npm --prefix contract run format

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

embed: ## Локальная очередь векторов: ARGS='--corpus-version UUID --drain', EMBED_RUN_FLAGS=-d для фона
	$(COMPOSE) run --rm --no-deps $(EMBED_RUN_FLAGS) worker python -m app.embed_cli $(ARGS)

link: ## Техническая очередь связывания: ARGS='--document UUID --model UUID --config /workspace/config.json --drain'
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace worker python -m app.link_cli $(ARGS)

verify-linking: verify-up ## Хранение, адаптер и worker link на отдельной PostgreSQL без внешних вызовов
	$(VERIFY_COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_linking_store.py

verify-review: verify-up ## Подтверждение, аудит, таймер и конкурентность без платных вызовов
	$(VERIFY_COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_review.py

configure-pipeline: ## Установить combined + Qwen из замороженной приёмки этапа 4
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m app.pipeline_admin --freeze docs/reports/stage4-linking-freeze-2026-11-03.json

accept-upload-start: ## Платная браузерная приёмка: временный worker на 10 минут, явный допуск и прежние бюджеты
	@test "$${PAID_CALLS_ENABLED:-false}" = "true"
	@test -n "$${BUDGET_RUB}"
	$(COMPOSE) stop worker
	$(COMPOSE) run -d --no-deps --name doclink-paid-upload -e PAID_CALLS_ENABLED -e BUDGET_RUB -e LLM_PROVIDER=live worker timeout --signal=TERM 600 python -m app.worker

accept-upload-stop: ## Остановить временную платную приёмку и вернуть штатный worker
	docker stop doclink-paid-upload
	docker rm doclink-paid-upload
	$(COMPOSE) up -d worker

verify-pipeline: verify-up ## Пакеты, зависимости и восстановление конвейера без платных вызовов
	$(VERIFY_COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_pipeline.py tests/test_documents.py

verify-api: verify-up ## REST, изоляция рабочих копий и безопасный импорт без платных вызовов
	$(VERIFY_COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_api.py tests/test_working_sets.py tests/test_corpus_archive.py

accept-working-set: ## Без оплаты: реальные копии через REST, проверка неизменности корпуса/вызовов; ARGS='--corpus-version UUID --document UUID --output report.json'
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -w /workspace worker python -m app.working_acceptance $(ARGS)

api-schema: ## Показать живые операции OpenAPI и коды ответов для ручной сверки контракта
	$(COMPOSE) exec -T api python -c 'import json, urllib.request; s=json.load(urllib.request.urlopen("http://localhost:8000/openapi.json")); print(json.dumps({p: {m: list(v["responses"]) for m,v in ops.items()} for p,ops in s["paths"].items()}, ensure_ascii=False, indent=2)); print(json.dumps(s["components"]["securitySchemes"]))'

accept-linking: ## Приёмка 4.6 без LLM: ARGS='tune|run|embedding-audit --corpus-version UUID --output report.json'
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -w /workspace worker python -m app.linking_acceptance $(ARGS)

verify-linking-acceptance: verify-up ## Tune/freeze/test, запрет stale input и повтор без моделей
	$(VERIFY_COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_linking_acceptance.py tests/test_extraction_audit.py

verify-embedding: verify-up ## Реальная проверка BGE-M3/pgvector/повтора на отдельной DB; сначала embedding-download
	$(VERIFY_COMPOSE) -f docker-compose.embedding-verify.yml run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_embedding_store.py -k real_embedding_cache

smoke-paid: ## Платный smoke: только при PAID_CALLS_ENABLED=true и BUDGET_RUB>0
	@test "$${PAID_CALLS_ENABLED:-false}" = "true" || { echo "PAID_CALLS_ENABLED must be true"; exit 1; }
	@test -n "$${BUDGET_RUB}" || { echo "BUDGET_RUB must be > 0"; exit 1; }
	$(COMPOSE) run --rm -e PAID_CALLS_ENABLED -e BUDGET_RUB -v "$(CURDIR):/workspace" -w /workspace api python -m app.smoke_paid $(ARGS)

extract: ## Очередь/продолжение/отчёт: ARGS='--document UUID --drain' (облачные вызовы — smoke-paid)
	$(COMPOSE) run --rm api python -m app.extract_cli $(ARGS)

freeze-extraction: ## Зафиксировать конфигурацию до test: ARGS='--corpus-version UUID'
	$(COMPOSE) run --rm api python -m app.extract_cli --freeze $(ARGS)

audit-extraction: ## Сохранить read-only JSON приёмки: ARGS='--corpus-version UUID --protocol-hash HASH --output docs/reports/report.json'
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -w /workspace api python -m app.extraction_audit $(ARGS)

transfer-extraction: ## Без сети: ARGS='preflight --source-version UUID --corpus-version UUID --output report.json', apply --input report.json или cards
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" -w /workspace api python -m app.transfer_cli $(ARGS)

verify-transfer: verify-up ## Перенос успехов/ошибок, stale preflight и история test без внешних вызовов
	$(VERIFY_COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python -m pytest -q tests/test_extraction_transfer.py

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

verify-migrations: verify-up ## Откат/повтор миграций 0009–0002, проверка metadata на изолированной БД
	$(VERIFY_COMPOSE) run --rm api alembic downgrade 0008
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head
	$(VERIFY_COMPOSE) run --rm api alembic downgrade 0007
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head
	$(VERIFY_COMPOSE) run --rm api alembic downgrade 0006
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head
	$(VERIFY_COMPOSE) run --rm api alembic downgrade 0005
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head
	$(VERIFY_COMPOSE) run --rm api alembic downgrade 0004
	$(VERIFY_COMPOSE) run --rm api alembic upgrade head
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
