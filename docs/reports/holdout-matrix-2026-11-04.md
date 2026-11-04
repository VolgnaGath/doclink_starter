# Отложенный test, прежние пороги

Dataset: `63825ea763184b904c296cb2ebbc3bce47045e66e6990d5f66657037abe9dc64`.
Корпус: `9acfe19b-89e2-43be-ae33-9169ebb8b519`.

Сравнение моделей условно на общей карточке Qwen.
Baselines общие для трёх моделей; не независимые повторы.
Трудные срезы пересекаются и не суммируются.
Кривые только tune; пороги по test не выбираются.
Макет параграфа 2.3 отсутствует; раскладка соответствует плану этапа 6.

## Таблица 2.6 — извлечение и исходные ресурсы моделей

| Модель / размещение | Split | N | Вид | Номер | Дата | ИНН | Сумма | Невалидные | Ненайденные цитаты | Нет оснований | мс/док. | ₽/док. | Локально, с (расчётное) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GigaChat 2 Lite / cloud | test | 221 | 1.00000 | 1.00000 | 1.00000 | 0.28507 | 0.93213 | 0.00000 | 0.07321 | 430 | 3607.94570 | — | — |
| GigaChat 2 Lite / cloud | tune | 0 | — | — | — | — | — | — | — | 0 | — | — | — |
| Qwen / открытые веса, облачное размещение | test | 221 | 1.00000 | 1.00000 | 1.00000 | 0.80543 | 0.93213 | 0.00000 | 0.02846 | 0 | 1813.62443 | 0.2056067873303167420814479638 | — |
| Qwen / открытые веса, облачное размещение | tune | 0 | — | — | — | — | — | — | — | 0 | — | — | — |
| YandexGPT 5.1 Pro / cloud | test | 221 | 0.99548 | 0.99548 | 0.99548 | 0.74208 | 0.92760 | 0.00452 | 0.30522 | 29 | 3077.26244 | 1.815399095022624434389140271 | — |
| YandexGPT 5.1 Pro / cloud | tune | 0 | — | — | — | — | — | — | — | 0 | — | — | — |

Знаменатели полей и цитат сохранены в CSV/манифесте; неразмеченные поля исключены.
Ресурсы — исходные физические вычисления, не время повторного извлечения из кэша.

## Таблица 2.7 — сравнение основных методов

| Вариант | Split | Статус | N | M | Верных | Gold | Precision | Recall | F1 | Охват | Док. точно | Разбор | Ошибки | Recall@3 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| combined/GigaChat 2 Lite/none | tune | done | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | test | done | 136 | 54 | 54 | 146 | 1.00000 | 0.36986 | 0.54000 | 0.39706 | 0.39706 | 0.54412 | 0 | 0.92857 |
| combined/Qwen/none | tune | done | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | test | done | 136 | 125 | 125 | 146 | 1.00000 | 0.85616 | 0.92251 | 0.89706 | 0.85294 | 0.08088 | 0 | 1.00000 |
| combined/YandexGPT 5.1 Pro/none | tune | done | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | done | 136 | 112 | 112 | 146 | 1.00000 | 0.76712 | 0.86822 | 0.79412 | 0.76471 | 0.17647 | 1 | 0.96154 |
| rules/shared/none (общий: Qwen/YandexGPT/GigaChat) | tune | done | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none (общий: Qwen/YandexGPT/GigaChat) | test | done | 136 | 60 | 60 | 146 | 1.00000 | 0.41096 | 0.58252 | 0.44118 | 0.42647 | 0.55882 | 0 | 0.32143 |
| semantic/shared/none (общий: Qwen/YandexGPT/GigaChat) | tune | done | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none (общий: Qwen/YandexGPT/GigaChat) | test | done | 136 | 10 | 8 | 146 | 0.80000 | 0.05479 | 0.10256 | 0.07353 | 0.04412 | 0.81618 | 0 | 1.00000 |

## Таблица 2.8 — абляции и трудные срезы

| Вариант | Split | Срез | N | M | Precision | Recall | F1 | Охват | Ошибки |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| combined/GigaChat 2 Lite/none | test | all | 136 | 54 | 1.00000 | 0.36986 | 0.54000 | 0.39706 | 0 |
| combined/GigaChat 2 Lite/none | test | hard | 52 | 8 | 1.00000 | 0.12903 | 0.22857 | 0.15385 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:1 | 26 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:2 | 15 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:3 | 10 | 1 | 1.00000 | 0.07143 | 0.13333 | 0.10000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:4 | 12 | 2 | 1.00000 | 0.14286 | 0.25000 | 0.16667 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:5 | 14 | 8 | 1.00000 | 0.57143 | 0.72727 | 0.57143 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:6 | 16 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:7 | 6 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:8 | 10 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | all | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | test | all | 136 | 128 | 0.99219 | 0.86986 | 0.92701 | 0.94118 | 0 |
| combined/Qwen/no_checks | test | hard | 52 | 44 | 0.97727 | 0.69355 | 0.81132 | 0.84615 | 0 |
| combined/Qwen/no_checks | test | hard:1 | 26 | 20 | 0.95000 | 0.63333 | 0.76000 | 0.76923 | 0 |
| combined/Qwen/no_checks | test | hard:2 | 15 | 10 | 1.00000 | 0.66667 | 0.80000 | 0.66667 | 0 |
| combined/Qwen/no_checks | test | hard:3 | 10 | 9 | 1.00000 | 0.64286 | 0.78261 | 0.90000 | 0 |
| combined/Qwen/no_checks | test | hard:4 | 12 | 9 | 1.00000 | 0.64286 | 0.78261 | 0.75000 | 0 |
| combined/Qwen/no_checks | test | hard:5 | 14 | 12 | 1.00000 | 0.85714 | 0.92308 | 0.85714 | 0 |
| combined/Qwen/no_checks | test | hard:6 | 16 | 12 | 0.91667 | 0.61111 | 0.73333 | 0.75000 | 0 |
| combined/Qwen/no_checks | test | hard:7 | 6 | 5 | 1.00000 | 0.71429 | 0.83333 | 0.83333 | 0 |
| combined/Qwen/no_checks | test | hard:8 | 10 | 10 | 1.00000 | 0.50000 | 0.66667 | 1.00000 | 0 |
| combined/Qwen/no_checks | test | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | all | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_llm | — | NO_FEASIBLE_CONFIGURATION: В исходном tune нет допустимой конфигурации; пороги не назначаются | — | — | — | — | — | — | — |
| combined/Qwen/none | test | all | 136 | 125 | 1.00000 | 0.85616 | 0.92251 | 0.89706 | 0 |
| combined/Qwen/none | test | hard | 52 | 41 | 1.00000 | 0.66129 | 0.79612 | 0.73077 | 0 |
| combined/Qwen/none | test | hard:1 | 26 | 20 | 1.00000 | 0.66667 | 0.80000 | 0.76923 | 0 |
| combined/Qwen/none | test | hard:2 | 15 | 5 | 1.00000 | 0.33333 | 0.50000 | 0.33333 | 0 |
| combined/Qwen/none | test | hard:3 | 10 | 10 | 1.00000 | 0.71429 | 0.83333 | 0.80000 | 0 |
| combined/Qwen/none | test | hard:4 | 12 | 10 | 1.00000 | 0.71429 | 0.83333 | 0.75000 | 0 |
| combined/Qwen/none | test | hard:5 | 14 | 12 | 1.00000 | 0.85714 | 0.92308 | 0.85714 | 0 |
| combined/Qwen/none | test | hard:6 | 16 | 12 | 1.00000 | 0.66667 | 0.80000 | 0.75000 | 0 |
| combined/Qwen/none | test | hard:7 | 6 | 5 | 1.00000 | 0.71429 | 0.83333 | 0.83333 | 0 |
| combined/Qwen/none | test | hard:8 | 10 | 12 | 1.00000 | 0.60000 | 0.75000 | 0.90000 | 0 |
| combined/Qwen/none | test | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | all | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | test | all | 136 | 96 | 1.00000 | 0.65753 | 0.79339 | 0.69853 | 0 |
| combined/Qwen/no_sem | test | hard | 52 | 14 | 1.00000 | 0.22581 | 0.36842 | 0.25000 | 0 |
| combined/Qwen/no_sem | test | hard:1 | 26 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | test | hard:2 | 15 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | test | hard:3 | 10 | 6 | 1.00000 | 0.42857 | 0.60000 | 0.50000 | 0 |
| combined/Qwen/no_sem | test | hard:4 | 12 | 4 | 1.00000 | 0.28571 | 0.44444 | 0.33333 | 0 |
| combined/Qwen/no_sem | test | hard:5 | 14 | 12 | 1.00000 | 0.85714 | 0.92308 | 0.85714 | 0 |
| combined/Qwen/no_sem | test | hard:6 | 16 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | test | hard:7 | 6 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | test | hard:8 | 10 | 2 | 1.00000 | 0.10000 | 0.18182 | 0.10000 | 0 |
| combined/Qwen/no_sem | test | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | all | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | all | 136 | 112 | 1.00000 | 0.76712 | 0.86822 | 0.79412 | 1 |
| combined/YandexGPT 5.1 Pro/none | test | hard | 52 | 39 | 1.00000 | 0.62903 | 0.77228 | 0.67308 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:1 | 26 | 21 | 1.00000 | 0.70000 | 0.82353 | 0.80769 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:2 | 15 | 4 | 1.00000 | 0.26667 | 0.42105 | 0.26667 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:3 | 10 | 8 | 1.00000 | 0.57143 | 0.72727 | 0.60000 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:4 | 12 | 9 | 1.00000 | 0.64286 | 0.78261 | 0.58333 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:5 | 14 | 12 | 1.00000 | 0.85714 | 0.92308 | 0.85714 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:6 | 16 | 11 | 1.00000 | 0.61111 | 0.75862 | 0.68750 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:7 | 6 | 3 | 1.00000 | 0.42857 | 0.60000 | 0.50000 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:8 | 10 | 12 | 1.00000 | 0.60000 | 0.75000 | 0.80000 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | all | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | test | all | 136 | 60 | 1.00000 | 0.41096 | 0.58252 | 0.44118 | 0 |
| rules/shared/none | test | hard | 52 | 10 | 1.00000 | 0.16129 | 0.27778 | 0.19231 | 0 |
| rules/shared/none | test | hard:1 | 26 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | test | hard:2 | 15 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | test | hard:3 | 10 | 4 | 1.00000 | 0.28571 | 0.44444 | 0.40000 | 0 |
| rules/shared/none | test | hard:4 | 12 | 3 | 1.00000 | 0.21429 | 0.35294 | 0.25000 | 0 |
| rules/shared/none | test | hard:5 | 14 | 8 | 1.00000 | 0.57143 | 0.72727 | 0.57143 | 0 |
| rules/shared/none | test | hard:6 | 16 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | test | hard:7 | 6 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | test | hard:8 | 10 | 2 | 1.00000 | 0.10000 | 0.18182 | 0.20000 | 0 |
| rules/shared/none | test | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | all | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | test | all | 136 | 10 | 0.80000 | 0.05479 | 0.10256 | 0.07353 | 0 |
| semantic/shared/none | test | hard | 52 | 6 | 1.00000 | 0.09677 | 0.17647 | 0.11538 | 0 |
| semantic/shared/none | test | hard:1 | 26 | 1 | 1.00000 | 0.03333 | 0.06452 | 0.03846 | 0 |
| semantic/shared/none | test | hard:2 | 15 | 1 | 1.00000 | 0.06667 | 0.12500 | 0.06667 | 0 |
| semantic/shared/none | test | hard:3 | 10 | 3 | 1.00000 | 0.21429 | 0.35294 | 0.30000 | 0 |
| semantic/shared/none | test | hard:4 | 12 | 2 | 1.00000 | 0.14286 | 0.25000 | 0.16667 | 0 |
| semantic/shared/none | test | hard:5 | 14 | 2 | 1.00000 | 0.14286 | 0.25000 | 0.14286 | 0 |
| semantic/shared/none | test | hard:6 | 16 | 1 | 1.00000 | 0.05556 | 0.10526 | 0.06250 | 0 |
| semantic/shared/none | test | hard:7 | 6 | 1 | 1.00000 | 0.14286 | 0.25000 | 0.16667 | 0 |
| semantic/shared/none | test | hard:8 | 10 | 2 | 1.00000 | 0.10000 | 0.18182 | 0.20000 | 0 |
| semantic/shared/none | test | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | all | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Контрольные случаи (split указан, это не отдельная test-метрика)

| Вариант | Документ | Split | Case | Ожидание | Исход | Причины | Пройдено | Результат |
|---|---|---|---|---|---|---|---|---|

## Ресурсы и воспроизводимость

Новые физические LLM-вызовы: 0; новый API-расход: 0 ₽.
Стоимость уникальных исходных вызовов серии: None ₽; фактическое денежное списание не установлено.

| Вариант | reused_test | τ_a | τ_r | Переиспользовано исходов | мс link | Хеш протокола |
|---|---|---:|---:|---:|---:|---|
| combined/GigaChat 2 Lite/none | False | 0.75074 | 0.15717 | 0 | 777 | 6e2b8d0881a0f5b185bf96929511dce2502d63bf950f4beba8f152c0dd6e37f7 |
| combined/Qwen/no_checks | False | 0.19620 | 0.18406 | 0 | 566 | 56ed284b7618f2e7ce36f30d9789af2e7ac7922f8fb07cf7550e91de6057a0b2 |
| combined/Qwen/no_llm | False | — | — | — | — | None |
| combined/Qwen/none | False | 0.19620 | 0.18406 | 0 | 594 | 680ed071e5167ccb8780bb2291e60fcbc4456c3d4a99e866fcc2cebfbfe8c2bb |
| combined/Qwen/no_sem | False | 0.45500 | 0.45500 | 0 | 534 | 5bd2027641788ebc6abedc95749100a544632a6b026ac68cb3963816dfdbe6bd |
| combined/YandexGPT 5.1 Pro/none | False | 0.19620 | 0.18406 | 0 | 549 | f8148569e8e6649a69a7dc0e752b54e55760b7778bf1d3c46c491b6836eab24a |
| rules/shared/none | False | 0.00000 | 0.00000 | 0 | 336 | 69f806ef31d720f5593bc871ae4b50fc3ae28e7b81286b30c0aa2a658b2a2719 |
| semantic/shared/none | False | 0.84094 | 0.71313 | 0 | 271 | 4df5cae10b949110994437100986a51f3bc954dda531ab789010481f6fb13c60 |
