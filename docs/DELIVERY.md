# Пакет сдачи DocLink

## Чистый основной запуск

Маршрут сборки, миграций, seed, импорта и BGE-M3 — в [README](../README.md).
Реальные модели/сертификаты/лимиты — в [MODELS.md](MODELS.md). Корпус и prompt seed
сами по себе не являются результатами моделей. Отчёт этапа 6 — не замена вычислений.

## Воспроизводимый перенос реальных вычислений

Существующая серия `f396bd92-1639-4193-87ae-d065bc0ee2b9` основана на реальных
облачных ответах и реальных BGE-M3-векторах. Команды ниже выполняются из корня:

```sh
make delivery-export ARGS='--series f396bd92-1639-4193-87ae-d065bc0ee2b9 --file data/delivery.json.gz --report docs/reports/stage8-artifact-export-2026-11-04.json'
make delivery-up
make delivery-restore ARGS='--file data/delivery.json.gz --report docs/reports/stage8-artifact-restore-2026-11-04.json'
make delivery-experiments ARGS='accept --repeat-of f396bd92-1639-4193-87ae-d065bc0ee2b9 --output docs/reports/stage8-clean-matrix-2026-11-04.json'
make delivery-prepare
make delivery-browser
make delivery-down
```

`data/` — локальная игнорируемая папка, экспорт создаёт её автоматически. Пакет gzip JSON
переносится отдельно от git, проверяется по bundle_hash, содержит полный FK-граф
происхождения, тексты и файлы **только обезличенного корпуса**, исходные физические
попытки/ошибки, извлечения, векторы, замороженную серию и рейтинги. Не содержит
anon_maps/anon_identities, пользовательских документов/сессий, конфигурации окружения
или ключей. При попадании не обезличенного документа экспорт отказывает.
Машинные пути файлов заменены SHA-256, при восстановлении содержимое проверяется.

Восстановление требует пустую БД после миграций, `DELIVERY_ACCEPTANCE_ENABLED=1`
в изолированном overlay; все записи одной транзакцией. Исходные UUID и история test
сохраняются. Никаких новых платных вызовов/улучшения двух ошибок. Явные serial ID
согласуются с sequence; runtime получает доступ к восстановленным blob-файлам.

На другой машине сначала загрузите веса через маршрут README и укажите
`EMBEDDING_MODEL_VOLUME` (по умолчанию `doclink_starter_models`). Это единственный
внешний том, он подключён только для чтения. БД, файлы, inbox и журнал 1С — новые.
Стенд слушает только localhost:28000/28001; пароль `verification-password`
предназначен только для этого изолированного стенда.

Replay не просто показывает старый отчёт: заново выполняются preflight, tune,
выбор модели, freeze/test и вычисление метрик по настоящим перенесённым артефактам;
проверяется сигнатура исходов/порогов/метрик/контрольных случаев. Новые ID серии и
тайминги дают новый dataset_hash, прежний dataset остаётся неизменным.

## Единый технический сценарий и граница LLM

`delivery-prepare` задаёт **явный regex** только новому рабочему потоку и создаёт
два новых документа в отдельном inbox. Worker: опрос → извлечение → реальный BGE-M3
→ предложение. Chromium: подтвердить → событие outbox → журнал mock1c.
Это единый технический сценарий, не новое облачное измерение. Качество LLM
проверяет отдельно replay полной реальной матрицы. Облачный сквозной сценарий
с новым документом потребовал бы отдельного допуска; текущие лимиты не повышаются.
Ранее оплаченный upload→confirm принят на этапе 5; обратная доставка — здесь.

## Человеческие замеры и калькулятор

На основном стенде зарегистрирован протокол
`reports/stage8-measurement-protocol-2026-11-04.json`: 9 копий, порядок URL фиксирован
до измерения. Человек открывает каждый URL, проверяет весь документ, принимает все
решения и завершает проверку. Первая сессия единственная; не заменять неудачную
быстрым повтором. Ошибок обработки в выбранном Qwen-test нет; эта категория p=0.

Расширение выполнено: **9 прежних + 21 новый = 30 выбранных документов**
(14 рекомендаций / 13 разборов / 3 без кандидата). Общий протокол и инструкция
с 21 новым URL: `reports/stage8-measurement-protocol-30-2026-11-04.{json,md}`.
Все 30 сессий завершены; 29 допустимы, №14 исключена как `different_outcome`
(error вместо review из-за неполного пула). Итог перезаписан в
`reports/stage8-human-effect-refresh-2026-11-04.{json,md}`: 14/12/3/0,
TO-BE 39,65 с, сценарная разница 173,27 ч/месяц, хеш `d39f3171...f55c2`.
Прежний 9-замерный снимок остаётся в Git.

Идемпотентная подготовка и обновление после завершения человеком:

```sh
make extend-measurements ARGS='--run 37a41963-0dce-4fbb-be64-94858a6f4a7a --base-protocol docs/reports/stage8-measurement-protocol-2026-11-04.json --allocation recommendation=11,review=10,none=0,error=0 --output docs/reports/stage8-measurement-protocol-30-2026-11-04.json'
make effect ARGS='snapshot --run 37a41963-0dce-4fbb-be64-94858a6f4a7a --protocol docs/reports/stage8-measurement-protocol-30-2026-11-04.json --output docs/reports/stage8-human-effect-refresh-2026-11-04.json'
make effect ARGS='replay --input docs/reports/stage8-human-effect-refresh-2026-11-04.json --output data/human-effect-replayed.json'
```

Вторую команду выполнять после новых замеров. Она перезапишет refresh JSON и Markdown,
сохранив ID реальных сессий/действий, исключения и неоткрытые документы двух наборов.
Прежняя версия остаётся в Git; метод и dataset серии не меняются.

Протокол подключается на `/effect` вставкой JSON или CLI `--protocol`:

```sh
make effect ARGS='snapshot --run 37a41963-0dce-4fbb-be64-94858a6f4a7a --protocol docs/reports/stage8-measurement-protocol-2026-11-04.json --output docs/reports/stage8-effect-2026-11-04.json'
make effect ARGS='replay --input docs/reports/stage8-effect-2026-11-04.json --output data/effect-replayed.json'
```

До получения замеров/ставок денежный эффект и экономия — null. Отдельный
`stage8-*-effect-*.json` из Chromium содержит **демонстрационные допущения**,
не человеческие измерения. Form/JSON/Markdown используют один snapshot/hash.

## Указатель материалов

| Материал | Артефакт |
|---|---|
| Таблицы 2.6–2.8 | `reports/stage6-matrix-2026-11-04.{md,csv,json}`, повтор на чистом стенде — `stage8-clean-matrix-*` |
| Рисунок 2.4: tune-кривая | `reports/stage6-matrix-2026-11-04.png` и `stage8-clean-matrix-2026-11-04.png` |
| 15 контролей + сверка 4.6 | `reports/stage6-audit-2026-11-04.json`, `stage6-acceptance-2026-11-04.md` |
| Схема таблиц/полей/связей | `DB_SCHEMA.md`, `make delivery-materials` |
| Приложения Г и Ж | `APPENDIX_G.md`, `APPENDIX_ZH.md`: назначение, строки, коммит файла, SHA-256 |
| Расчёт без замены неизвестного нулями | `reports/stage8-effect-2026-11-04.{json,md}` |
| Человеческий протокол | `reports/stage8-measurement-protocol-2026-11-04.json`, расширение — `stage8-measurement-protocol-30-2026-11-04.*`, итог — `stage8-human-effect-refresh-2026-11-04.*` |
| Манифест с SHA-256 | `reports/stage8-manifest-2026-11-04.json` |

### Подписи к скриншотам

- `stage6-browser-2026-11-04-progress.png`: постановка и ход полной матрицы.
- `stage6-browser-2026-11-04.png`: завершённая матрица, общий dataset и выгрузки.
- `stage8-delivery-proposal-2026-11-04.png`: новый документ на чистом стенде,
  рекомендация до подтверждения (связи ещё нет).
- `stage8-delivery-confirmed-2026-11-04.png`: подтверждённая связь и доставка в mock1c.
- `stage8-delivery-effect-2026-11-04-missing.png`: отсутствующие замеры/денежные входы.
- `stage8-delivery-effect-2026-11-04-cloud.png`: облачный расчёт на демонстрационных допущениях.
- `stage8-delivery-effect-2026-11-04-server.png`: серверный сценарий, локальное время только расчётное.

Внешний макет §2.3 и приложений не получен; редакционная раскладка предварительная.
Сдача и цель качества разделены: Precision=0,92632, целевые 98% не достигнуты.
