# CLAUDE.md — DocLink

Сервис установления связей между документами договорного процесса (договор, допсоглашение, счёт, акт, УПД).
Он одновременно служит прототипом для магистерской диссертации и стендом для эксперимента.
Полное ТЗ: https://claude.ai/artifact/JE8ibRrgWJysFLNRkfdWQt (копию положить в docs/TZ.md). При расхождении приоритет у ТЗ.

## Решения (31.10.2026, приоритет над всем остальным)
- **Схема работы:** загрузка → извлечение LLM → карточка заполняется автоматически → поиск договоров → рекомендация →
  подтверждение бухгалтером. Бухгалтер может исправить карточку, после чего поиск запускается заново.
- **Вид и дата** берутся из карточки, заполненной извлечением (LLM; в варианте «без LLM» — регулярки). Эталон методу
  не подставляется. Нет вида или даты — исход «разбор», выдуманное значение не подставляется.
- **Единое правило дат:** кандидат — договор того же клиента с датой (из карточки) не позже даты документа. Без допуска.
  `registered_at` — только аудит.
- **Цель — только договор.** Косвенных путей (через допсоглашение или счёт) в v1 нет: каждый документ сопоставляется с
  договорами напрямую; при недостатке оснований — разбор.
- **Все связи подтверждает бухгалтер.** Очередь содержит все обработанные документы: рекомендации, разбор, «нет
  кандидата», ошибки. В `links` только `confirmed`/`manual` со ссылкой на `review_actions`.
- **Тип 8 по ссылкам:** две отдельные ссылки, каждая однозначно указывает на свой договор → две рекомендации; одна ссылка
  и два подходящих договора → альтернативы, не рекомендация; три и более договора → ручной разбор.
- **Номер и дата одной ссылки** оцениваются вместе. Есть явная ссылка с номером, а договору не соответствует ни одна →
  противоречие: только разбор с пояснением, не рекомендация. Номер: сырой 1,0; после нормализации OCR 0,9.
- **Ошибка обработки** (таймаут, невалидный ответ) после ограниченных повторов → статус «Ошибка обработки», можно
  повторить вручную. Документ остаётся в числе входов; при наличии эталонного договора — пропуск в Recall; в M не входит.
  Автоматического перехода на другой метод нет.
- **Метрики:** Precision = верные рекомендованные связи / все рекомендованные (M=0 → 0, M показывается, доля ошибок «—»);
  Recall = найденные верные / все эталонные связи; охват = документы хотя бы с одной рекомендацией / все входящие
  документы (включая без договора и ошибки; договоры-кандидаты не входят); «документ верно» = все нужные связи и ни одной
  лишней. Альтернативы для ручного выбора рекомендациями не считаются.
- **reference/linker.py** — вспомогательный материал, совпадение с ним не требуется. Правильность проверяется на
  fixtures с известным ответом (по одному на каждое правило выше).
- Исследования «ручной режим против assisted» нет; время подтверждения пишется в `review_actions`.

## Главные правила
1. **Честность эксперимента.** Метрики считаются только по реальным вызовам моделей. Никаких заглушек, которые
   возвращают «правдоподобные» числа. Ошибки обработки учитываются как описано в разделе «Решения».
2. **Без утечки из будущего.** Кандидаты — договоры того же клиента с датой не позже даты документа (см. «Решения»). Пороги подбираются только на `split=tune`, метрики считаются на `split=test`.
3. **Эталон.** `reference/linker.py` и `reference/run_models.py` — вспомогательные; нормативны ТЗ и раздел «Решения».
4. **Всё логируется.** Каждый вызов модели пишется в `llm_calls` (токены, задержка, стоимость, сырой ответ), каждое
   решение — в `link_suggestions` с признаками, каждое действие бухгалтера — в `review_actions` с длительностью.
5. **Секреты.** Ключи только в `.env`. Не логировать заголовки авторизации, не писать ключи в БД.
6. **Персональные данные.** Перед внешним API текст обезличивается (`services/anonymize.py`), если у документа не
   стоит `is_anonymized=true`. Таблица `anon_maps` никуда не выгружается.
7. **Размещение моделей.** Qwen в Yandex AI Studio — это `placement=open-weights-cloud`: в отчётах подписывать
   «открытые веса, облачное размещение». Время локального запуска только *оценивается* формулой из ТЗ (Ф9),
   его нельзя выдавать за замер.

8. **Эталон изолирован.** Эталонные связи, deal_id, split, hard_types, имена папок не попадают в вход движка,
   промпт и векторы. Модуль оценки не импортируется движком связывания.
9. **Сбой ≠ отказ.** См. «Решения». Неизвестная стоимость — null, не 0. Ошибки не кэшируются как успех.
10. **Платные вызовы под замком.** По умолчанию провайдер `mock`; живые вызовы только при `PAID_CALLS_ENABLED=true`
    и положительном `BUDGET_RUB`. `make test` не делает платных запросов; отдельная команда `make smoke-paid`.
11. **Фиксация протокола.** Перед прогоном test конфигурация (веса, пороги, промпт, модель) замораживается по хешу;
    после просмотра test любое изменение метода — новый прогон с пометкой `reused_test`.
12. **Обрезка текста явная.** Если текст обрезан (6000 + 2500 символов), это пишется в `llm_calls.truncated`.
13. **IMPLEMENTATION_STATUS.md.** Вести таблицу этапов: сделано, что реально проверено, что не запускалось и почему.

## Стек
Python 3.12 · FastAPI + Uvicorn · Jinja2 + HTMX (без SPA) · SQLAlchemy 2 + Alembic · PostgreSQL 16 + pgvector ·
psycopg 3 · Pydantic v2 · httpx · pdfplumber, pypdfium2, pytesseract (rus), python-docx · sentence-transformers
(BGE-M3, CPU) · numpy, scikit-learn, matplotlib · pytest · ruff. Очередь — таблица `jobs` (`FOR UPDATE SKIP LOCKED`).

## Структура репозитория
```
doclink/
  app/
    main.py                # FastAPI, подключение роутеров, шаблонов
    config.py              # pydantic-settings из .env
    db/ models.py session.py
    api/ documents.py suggestions.py experiments.py corpus.py health.py
    web/ routes.py templates/ static/   # HTMX-страницы из ТЗ 4.5
    services/
      ingest.py            # загрузка, sha256, хранение файлов
      text.py              # pdfplumber + OCR
      anonymize.py         # порт anonymize_pdf.anonymize + обратная замена
      llm/ base.py yandex.py gigachat.py ollama.py regex.py registry.py
      extraction.py        # промпт, схема, валидация, проверка цитат, кэш
      embeddings.py        # BGE-M3 / TF-IDF
      linking/ normalize.py candidates.py features.py checks.py scoring.py decide.py
      baselines/ rules.py semantic.py
      experiments/ runner.py metrics.py curves.py export.py
      economics.py         # расчёт эффекта, N* = C_f / c
    connectors/ onec_client.py          # опрос mock1c
    worker.py              # цикл обработки jobs
  mock1c/ app.py inbox/                 # заглушка OData
  prompts/ extract_v1.txt extract_v1.schema.json
  reference/ linker.py run_models.py anonymize_pdf.py   # эталон, не менять
  corpus/                                # монтируется томом (real/, synth/)
  migrations/
  tests/
  docker-compose.yml  Dockerfile  .env.example  Makefile  pyproject.toml  README.md
```

## Команды
```
make up            # docker compose up -d --build
make migrate       # alembic upgrade head
make seed          # конфигурации моделей и промпт v1
make import-corpus # импорт ./corpus, split seed=2026, tune_share=0.3
make test          # pytest
make lint          # ruff check . && ruff format --check .
docker compose --profile ollama up -d   # необязательная локальная модель
```

## Переменные окружения (.env.example)
```
DATABASE_URL=postgresql+psycopg://doclink:doclink@db:5432/doclink
APP_USER=admin
APP_PASSWORD=change-me
SECRET_KEY=change-me
YC_API_KEY=
YC_FOLDER_ID=
GIGACHAT_AUTH_KEY=          # base64(client_id:client_secret)
GIGACHAT_SCOPE=GIGACHAT_API_PERS
PAID_CALLS_ENABLED=false
BUDGET_RUB=0
OLLAMA_URL=http://ollama:11434
EMBEDDER=bge-m3             # bge-m3 | tfidf
FILES_DIR=/data/files
MOCK1C_URL=http://mock1c:8001
MOCK1C_POLL_SECONDS=30
```

## Конфигурации моделей (seed)
| name | provider | model_uri | placement |
|---|---|---|---|
| qwen-open | yandex | gpt://{folder}/<qwen из AI Studio>/latest ⚠ уточнить | open-weights-cloud |
| yandexgpt-pro | yandex | gpt://{folder}/yandexgpt/latest ⚠ уточнить версию 5.1 | cloud |
| gigachat-lite | gigachat | GigaChat-2 ⚠ уточнить id Lite | cloud |
| lightning-local | ollama | hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:Q4_K_M | local (необязательно) |
| regex | regex | — | local |
| mock | mock | — | local (по умолчанию, для тестов) |
Тарифы (`price_in_1k`, `price_out_1k`) заполняет пользователь по прайсам провайдеров.

## Метод (кратко)
- Пары: допсоглашение→договор «изменение», счёт→договор «основание», акт/УПД→договор «исполнение».
- Признаки: num и date — по лучшей ссылке (номер и дата одной ссылки вместе): num 1 сырой / 0.9 нормализованный /
  0.8 префикс / 0.7 при сходстве ≥0.8; date 1 / 0.5 при ±10 дн.; inn; name; sem (косинус BGE-M3).
- S = 0.45·num + 0.15·date + 0.15·inn + 0.05·name + 0.20·sem − P; штрафы: сумма > 1.25×цены договора −0.2, неверный ИНН −0.05.
- Решение: S ≥ τ_a → рекомендация; τ_r ≤ S < τ_a → разбор; иначе «нет кандидата»; сбой → «ошибка». Равные лидеры,
  противоречие ссылки и неоднозначность типа 8 — только разбор. τ_a — максимум охвата при Precision ≥ 0.98 и M>0 на tune;
  нет такого — NO_FEASIBLE_CONFIGURATION.

## Метрики прогона (experiment_metrics, срезы: all, real, synth, hard, type_1..9, kind_*)
M, precision, error_share, recall, f1, f1_hard, coverage, doc_exact, review_share, error_count, recall_at_3_review, extraction_acc_{kind,number,date,inn,amount},
invalid_json_rate, evidence_miss_rate, sec_per_doc, cost_per_doc_rub, tokens_per_doc, est_local_sec_per_doc.

## План реализации
Работать строго по этапам ТЗ, раздел 5. Каждый этап — отдельная ветка и PR, тесты зелёные.
- [ ] 0. Каркас: compose (api, worker, db, mock1c), Dockerfile с tesseract-ocr-rus, Alembic, вход, /health
- [ ] 1. Модели БД, загрузка, импорт корпуса (231 документ в оценке, 156 связей), текст/OCR, список и карточка
- [ ] 2. Провайдеры LLM + llm_calls + кэш по input_hash; обезличивание; промпт v1; валидация; проверка цитат
- [ ] 3. Эмбеддинги BGE-M3 → pgvector; метод по разделу «Решения»; rules/semantic; fixtures с известным ответом
- [ ] 4. Очередь подтверждения (рекомендации и разбор), карточка с кандидатами, таймер, запись только подтверждённых связей
- [ ] 5. Эксперименты: прогоны, подбор τ, абляции, срезы, кривые, сравнение, экспорт CSV/MD/PNG
- [ ] 6. mock1c + коннектор с опросом + обратная запись связей
- [ ] 7. Страница расчёта эффекта, README, скриншоты (прил. Ж), фрагменты кода (прил. Г)

## Стиль
- Типы везде, функции метода чистые (без БД) в `services/linking/*` — их легко тестировать и сравнивать с эталоном.
- Интерфейс на русском. Даты ДД.ММ.ГГГГ, суммы с пробелами-разделителями.
- Не добавлять зависимостей без необходимости; не менять файлы в `reference/`.
- Коммиты небольшие, по смыслу; в описании PR — что проверено.
