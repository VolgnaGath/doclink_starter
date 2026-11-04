# Браузерная приёмка этапа 6

Dataset: `6364084c1961e170016b23a6333109b70f83b547620360656b6f417a754c7298`.
Корпус: `196ecb8f-fe6e-41c6-8785-a4faf69a6b1f`.

Сравнение моделей условно на общей карточке Qwen.
Baselines общие для трёх моделей; не независимые повторы.
Трудные срезы пересекаются и не суммируются.
Кривые только tune; пороги по test не выбираются.
Макет параграфа 2.3 отсутствует; раскладка соответствует плану этапа 6.

## Таблица 2.6 — извлечение и исходные ресурсы моделей

| Модель / размещение | Split | N | Вид | Номер | Дата | ИНН | Сумма | Невалидные | Ненайденные цитаты | Нет оснований | мс/док. | ₽/док. | Локально, с (расчётное) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GigaChat 2 Lite / cloud | test | 190 | 0.99474 | 1.00000 | 1.00000 | 0.32402 | 0.96089 | 0.00000 | 0.08759 | 361 | 2656.27895 | 0.05434000 | — |
| GigaChat 2 Lite / cloud | tune | 60 | 1.00000 | 1.00000 | 1.00000 | 0.40000 | 0.90909 | 0.00000 | 0.08046 | 117 | 2852.05000 | 0.06376825 | — |
| Qwen / открытые веса, облачное размещение | test | 190 | 0.99474 | 0.99468 | 0.98925 | 0.83240 | 0.96089 | 0.00000 | 0.01269 | 0 | 1098.15263 | 0.2204484210526315789473684211 | — |
| Qwen / открытые веса, облачное размещение | tune | 60 | 1.00000 | 0.98333 | 1.00000 | 0.78182 | 0.90909 | 0.00000 | 0.01091 | 0 | 1062.08333 | 0.28238000 | — |
| YandexGPT 5.1 Pro / cloud | test | 190 | 0.99474 | 0.99468 | 0.98925 | 0.82682 | 0.95531 | 0.00526 | 0.25610 | 44 | 2750.10526 | 1.672016842105263157894736842 | — |
| YandexGPT 5.1 Pro / cloud | tune | 60 | 0.98333 | 0.98333 | 0.98333 | 0.78182 | 0.90909 | 0.01667 | 0.25660 | 4 | 2929.48333 | 1.660866666666666666666666667 | — |

Знаменатели полей и цитат сохранены в CSV/манифесте; неразмеченные поля исключены.
Ресурсы — исходные физические вычисления, не время повторного извлечения из кэша.

## Таблица 2.7 — сравнение основных методов

| Вариант | Split | Статус | N | M | Верных | Gold | Precision | Recall | F1 | Охват | Док. точно | Разбор | Ошибки | Recall@3 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| combined/GigaChat 2 Lite/none | tune | done | 34 | 12 | 12 | 34 | 1.00000 | 0.35294 | 0.52174 | 0.35294 | 0.41176 | 0.64706 | 0 | 0.86364 |
| combined/GigaChat 2 Lite/none | test | done | 119 | 47 | 47 | 137 | 1.00000 | 0.34307 | 0.51087 | 0.38655 | 0.42017 | 0.60504 | 0 | 0.78889 |
| combined/Qwen/none | tune | done | 34 | 28 | 28 | 34 | 1.00000 | 0.82353 | 0.90323 | 0.82353 | 0.82353 | 0.17647 | 0 | 1.00000 |
| combined/Qwen/none | test | done | 119 | 95 | 88 | 137 | 0.92632 | 0.64234 | 0.75862 | 0.78151 | 0.67227 | 0.19328 | 0 | 0.82143 |
| combined/YandexGPT 5.1 Pro/none | tune | done | 34 | 27 | 27 | 34 | 1.00000 | 0.79412 | 0.88525 | 0.79412 | 0.79412 | 0.17647 | 1 | 1.00000 |
| combined/YandexGPT 5.1 Pro/none | test | done | 119 | 85 | 82 | 137 | 0.96471 | 0.59854 | 0.73874 | 0.69748 | 0.61345 | 0.26891 | 1 | 0.84211 |
| rules/shared/none (общий: Qwen/YandexGPT/GigaChat) | tune | done | 34 | 13 | 13 | 34 | 1.00000 | 0.38235 | 0.55319 | 0.38235 | 0.44118 | 0.61765 | 0 | 0.42857 |
| rules/shared/none (общий: Qwen/YandexGPT/GigaChat) | test | done | 119 | 41 | 41 | 137 | 1.00000 | 0.29927 | 0.46067 | 0.34454 | 0.36975 | 0.64706 | 0 | 0.40426 |
| semantic/shared/none (общий: Qwen/YandexGPT/GigaChat) | tune | done | 34 | 3 | 3 | 34 | 1.00000 | 0.08824 | 0.16216 | 0.08824 | 0.14706 | 0.88235 | 0 | 1.00000 |
| semantic/shared/none (общий: Qwen/YandexGPT/GigaChat) | test | done | 119 | 7 | 3 | 137 | 0.42857 | 0.02190 | 0.04167 | 0.05882 | 0.04202 | 0.84034 | 0 | 0.88235 |

## Таблица 2.8 — абляции и трудные срезы

| Вариант | Split | Срез | N | M | Precision | Recall | F1 | Охват | Ошибки |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| combined/GigaChat 2 Lite/none | test | all | 119 | 47 | 1.00000 | 0.34307 | 0.51087 | 0.38655 | 0 |
| combined/GigaChat 2 Lite/none | test | hard | 51 | 8 | 1.00000 | 0.10959 | 0.19753 | 0.13725 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:1 | 20 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:2 | 14 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:3 | 7 | 4 | 1.00000 | 0.36364 | 0.53333 | 0.42857 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:4 | 10 | 2 | 1.00000 | 0.20000 | 0.33333 | 0.20000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:5 | 18 | 4 | 1.00000 | 0.16667 | 0.28571 | 0.16667 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:6 | 17 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:7 | 7 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:8 | 19 | 2 | 1.00000 | 0.04878 | 0.09302 | 0.05263 | 0 |
| combined/GigaChat 2 Lite/none | test | hard:9 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | tune | all | 34 | 12 | 1.00000 | 0.35294 | 0.52174 | 0.35294 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard | 15 | 2 | 1.00000 | 0.11765 | 0.21053 | 0.13333 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:1 | 4 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:2 | 4 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:3 | 1 | 1 | 1.00000 | 1.00000 | 1.00000 | 1.00000 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:4 | 6 | 1 | 1.00000 | 0.16667 | 0.28571 | 0.16667 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:5 | 4 | 1 | 1.00000 | 0.16667 | 0.28571 | 0.25000 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:7 | 3 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:8 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/GigaChat 2 Lite/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | test | all | 119 | 105 | 0.90476 | 0.69343 | 0.78512 | 0.88235 | 0 |
| combined/Qwen/no_checks | test | hard | 51 | 42 | 0.76190 | 0.43836 | 0.55652 | 0.82353 | 0 |
| combined/Qwen/no_checks | test | hard:1 | 20 | 17 | 0.64706 | 0.32353 | 0.43137 | 0.85000 | 0 |
| combined/Qwen/no_checks | test | hard:2 | 14 | 10 | 0.60000 | 0.37500 | 0.46154 | 0.71429 | 0 |
| combined/Qwen/no_checks | test | hard:3 | 7 | 6 | 1.00000 | 0.54545 | 0.70588 | 0.85714 | 0 |
| combined/Qwen/no_checks | test | hard:4 | 10 | 7 | 0.85714 | 0.60000 | 0.70588 | 0.70000 | 0 |
| combined/Qwen/no_checks | test | hard:5 | 18 | 17 | 0.70588 | 0.50000 | 0.58537 | 0.94444 | 0 |
| combined/Qwen/no_checks | test | hard:6 | 17 | 13 | 0.69231 | 0.31034 | 0.42857 | 0.76471 | 0 |
| combined/Qwen/no_checks | test | hard:7 | 7 | 7 | 0.57143 | 0.40000 | 0.47059 | 1.00000 | 0 |
| combined/Qwen/no_checks | test | hard:8 | 19 | 19 | 0.78947 | 0.36585 | 0.50000 | 1.00000 | 0 |
| combined/Qwen/no_checks | test | hard:9 | 2 | 2 | 0.50000 | 0.50000 | 0.50000 | 1.00000 | 0 |
| combined/Qwen/no_checks | tune | all | 34 | 31 | 1.00000 | 0.91176 | 0.95385 | 0.91176 | 0 |
| combined/Qwen/no_checks | tune | hard | 15 | 14 | 1.00000 | 0.82353 | 0.90323 | 0.93333 | 0 |
| combined/Qwen/no_checks | tune | hard:1 | 4 | 3 | 1.00000 | 0.50000 | 0.66667 | 0.75000 | 0 |
| combined/Qwen/no_checks | tune | hard:2 | 4 | 4 | 1.00000 | 1.00000 | 1.00000 | 1.00000 | 0 |
| combined/Qwen/no_checks | tune | hard:3 | 1 | 1 | 1.00000 | 1.00000 | 1.00000 | 1.00000 | 0 |
| combined/Qwen/no_checks | tune | hard:4 | 6 | 5 | 1.00000 | 0.83333 | 0.90909 | 0.83333 | 0 |
| combined/Qwen/no_checks | tune | hard:5 | 4 | 4 | 1.00000 | 0.66667 | 0.80000 | 1.00000 | 0 |
| combined/Qwen/no_checks | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_checks | tune | hard:7 | 3 | 3 | 1.00000 | 0.75000 | 0.85714 | 1.00000 | 0 |
| combined/Qwen/no_checks | tune | hard:8 | 2 | 2 | 1.00000 | 0.50000 | 0.66667 | 1.00000 | 0 |
| combined/Qwen/no_checks | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_llm | — | NO_FEASIBLE_CONFIGURATION: Нет τ_a с Precision≥0,98 и M>0 на tune | — | — | — | — | — | — | — |
| combined/Qwen/none | test | all | 119 | 95 | 0.92632 | 0.64234 | 0.75862 | 0.78151 | 0 |
| combined/Qwen/none | test | hard | 51 | 32 | 0.78125 | 0.34247 | 0.47619 | 0.58824 | 0 |
| combined/Qwen/none | test | hard:1 | 20 | 16 | 0.68750 | 0.32353 | 0.44000 | 0.80000 | 0 |
| combined/Qwen/none | test | hard:2 | 14 | 5 | 0.60000 | 0.18750 | 0.28571 | 0.35714 | 0 |
| combined/Qwen/none | test | hard:3 | 7 | 8 | 1.00000 | 0.72727 | 0.84211 | 0.85714 | 0 |
| combined/Qwen/none | test | hard:4 | 10 | 4 | 1.00000 | 0.40000 | 0.57143 | 0.40000 | 0 |
| combined/Qwen/none | test | hard:5 | 18 | 14 | 0.57143 | 0.33333 | 0.42105 | 0.66667 | 0 |
| combined/Qwen/none | test | hard:6 | 17 | 12 | 0.75000 | 0.31034 | 0.43902 | 0.70588 | 0 |
| combined/Qwen/none | test | hard:7 | 7 | 7 | 0.57143 | 0.40000 | 0.47059 | 1.00000 | 0 |
| combined/Qwen/none | test | hard:8 | 19 | 17 | 0.82353 | 0.34146 | 0.48276 | 0.78947 | 0 |
| combined/Qwen/none | test | hard:9 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/none | tune | all | 34 | 28 | 1.00000 | 0.82353 | 0.90323 | 0.82353 | 0 |
| combined/Qwen/none | tune | hard | 15 | 11 | 1.00000 | 0.64706 | 0.78571 | 0.73333 | 0 |
| combined/Qwen/none | tune | hard:1 | 4 | 3 | 1.00000 | 0.50000 | 0.66667 | 0.75000 | 0 |
| combined/Qwen/none | tune | hard:2 | 4 | 1 | 1.00000 | 0.25000 | 0.40000 | 0.25000 | 0 |
| combined/Qwen/none | tune | hard:3 | 1 | 1 | 1.00000 | 1.00000 | 1.00000 | 1.00000 | 0 |
| combined/Qwen/none | tune | hard:4 | 6 | 5 | 1.00000 | 0.83333 | 0.90909 | 0.83333 | 0 |
| combined/Qwen/none | tune | hard:5 | 4 | 4 | 1.00000 | 0.66667 | 0.80000 | 1.00000 | 0 |
| combined/Qwen/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/none | tune | hard:7 | 3 | 3 | 1.00000 | 0.75000 | 0.85714 | 1.00000 | 0 |
| combined/Qwen/none | tune | hard:8 | 2 | 2 | 1.00000 | 0.50000 | 0.66667 | 1.00000 | 0 |
| combined/Qwen/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | test | all | 119 | 74 | 1.00000 | 0.54015 | 0.70142 | 0.60504 | 0 |
| combined/Qwen/no_sem | test | hard | 51 | 11 | 1.00000 | 0.15068 | 0.26190 | 0.17647 | 0 |
| combined/Qwen/no_sem | test | hard:1 | 20 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | test | hard:2 | 14 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | test | hard:3 | 7 | 6 | 1.00000 | 0.54545 | 0.70588 | 0.57143 | 0 |
| combined/Qwen/no_sem | test | hard:4 | 10 | 3 | 1.00000 | 0.30000 | 0.46154 | 0.30000 | 0 |
| combined/Qwen/no_sem | test | hard:5 | 18 | 6 | 1.00000 | 0.25000 | 0.40000 | 0.22222 | 0 |
| combined/Qwen/no_sem | test | hard:6 | 17 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | test | hard:7 | 7 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | test | hard:8 | 19 | 4 | 1.00000 | 0.09756 | 0.17778 | 0.10526 | 0 |
| combined/Qwen/no_sem | test | hard:9 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | tune | all | 34 | 24 | 1.00000 | 0.70588 | 0.82759 | 0.70588 | 0 |
| combined/Qwen/no_sem | tune | hard | 15 | 7 | 1.00000 | 0.41176 | 0.58333 | 0.46667 | 0 |
| combined/Qwen/no_sem | tune | hard:1 | 4 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | tune | hard:2 | 4 | 1 | 1.00000 | 0.25000 | 0.40000 | 0.25000 | 0 |
| combined/Qwen/no_sem | tune | hard:3 | 1 | 1 | 1.00000 | 1.00000 | 1.00000 | 1.00000 | 0 |
| combined/Qwen/no_sem | tune | hard:4 | 6 | 4 | 1.00000 | 0.66667 | 0.80000 | 0.66667 | 0 |
| combined/Qwen/no_sem | tune | hard:5 | 4 | 2 | 1.00000 | 0.33333 | 0.50000 | 0.50000 | 0 |
| combined/Qwen/no_sem | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/Qwen/no_sem | tune | hard:7 | 3 | 1 | 1.00000 | 0.25000 | 0.40000 | 0.33333 | 0 |
| combined/Qwen/no_sem | tune | hard:8 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/Qwen/no_sem | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | all | 119 | 85 | 0.96471 | 0.59854 | 0.73874 | 0.69748 | 1 |
| combined/YandexGPT 5.1 Pro/none | test | hard | 51 | 28 | 0.89286 | 0.34247 | 0.49505 | 0.50980 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:1 | 20 | 15 | 0.80000 | 0.35294 | 0.48980 | 0.75000 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:2 | 14 | 5 | 0.60000 | 0.18750 | 0.28571 | 0.35714 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:3 | 7 | 8 | 1.00000 | 0.72727 | 0.84211 | 0.85714 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:4 | 10 | 3 | 1.00000 | 0.30000 | 0.46154 | 0.30000 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:5 | 18 | 11 | 0.72727 | 0.33333 | 0.45714 | 0.50000 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:6 | 17 | 11 | 0.90909 | 0.34483 | 0.50000 | 0.64706 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:7 | 7 | 4 | 1.00000 | 0.40000 | 0.57143 | 0.57143 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:8 | 19 | 16 | 0.93750 | 0.36585 | 0.52632 | 0.73684 | 0 |
| combined/YandexGPT 5.1 Pro/none | test | hard:9 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | all | 34 | 27 | 1.00000 | 0.79412 | 0.88525 | 0.79412 | 1 |
| combined/YandexGPT 5.1 Pro/none | tune | hard | 15 | 10 | 1.00000 | 0.58824 | 0.74074 | 0.66667 | 1 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:1 | 4 | 3 | 1.00000 | 0.50000 | 0.66667 | 0.75000 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:2 | 4 | 1 | 1.00000 | 0.25000 | 0.40000 | 0.25000 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:3 | 1 | 1 | 1.00000 | 1.00000 | 1.00000 | 1.00000 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:4 | 6 | 4 | 1.00000 | 0.66667 | 0.80000 | 0.66667 | 1 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:5 | 4 | 4 | 1.00000 | 0.66667 | 0.80000 | 1.00000 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:7 | 3 | 3 | 1.00000 | 0.75000 | 0.85714 | 1.00000 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:8 | 2 | 2 | 1.00000 | 0.50000 | 0.66667 | 1.00000 | 0 |
| combined/YandexGPT 5.1 Pro/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | test | all | 119 | 41 | 1.00000 | 0.29927 | 0.46067 | 0.34454 | 0 |
| rules/shared/none | test | hard | 51 | 4 | 1.00000 | 0.05479 | 0.10390 | 0.07843 | 0 |
| rules/shared/none | test | hard:1 | 20 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | test | hard:2 | 14 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | test | hard:3 | 7 | 3 | 1.00000 | 0.27273 | 0.42857 | 0.42857 | 0 |
| rules/shared/none | test | hard:4 | 10 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | test | hard:5 | 18 | 2 | 1.00000 | 0.08333 | 0.15385 | 0.11111 | 0 |
| rules/shared/none | test | hard:6 | 17 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | test | hard:7 | 7 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | test | hard:8 | 19 | 1 | 1.00000 | 0.02439 | 0.04762 | 0.05263 | 0 |
| rules/shared/none | test | hard:9 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | tune | all | 34 | 13 | 1.00000 | 0.38235 | 0.55319 | 0.38235 | 0 |
| rules/shared/none | tune | hard | 15 | 4 | 1.00000 | 0.23529 | 0.38095 | 0.26667 | 0 |
| rules/shared/none | tune | hard:1 | 4 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | tune | hard:2 | 4 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | tune | hard:3 | 1 | 1 | 1.00000 | 1.00000 | 1.00000 | 1.00000 | 0 |
| rules/shared/none | tune | hard:4 | 6 | 2 | 1.00000 | 0.33333 | 0.50000 | 0.33333 | 0 |
| rules/shared/none | tune | hard:5 | 4 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| rules/shared/none | tune | hard:7 | 3 | 1 | 1.00000 | 0.25000 | 0.40000 | 0.33333 | 0 |
| rules/shared/none | tune | hard:8 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| rules/shared/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | test | all | 119 | 7 | 0.42857 | 0.02190 | 0.04167 | 0.05882 | 0 |
| semantic/shared/none | test | hard | 51 | 5 | 0.40000 | 0.02740 | 0.05128 | 0.09804 | 0 |
| semantic/shared/none | test | hard:1 | 20 | 2 | 1.00000 | 0.05882 | 0.11111 | 0.10000 | 0 |
| semantic/shared/none | test | hard:2 | 14 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| semantic/shared/none | test | hard:3 | 7 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| semantic/shared/none | test | hard:4 | 10 | 1 | 0.00000 | 0.00000 | 0 | 0.10000 | 0 |
| semantic/shared/none | test | hard:5 | 18 | 2 | 0.00000 | 0.00000 | 0 | 0.11111 | 0 |
| semantic/shared/none | test | hard:6 | 17 | 1 | 1.00000 | 0.03448 | 0.06667 | 0.05882 | 0 |
| semantic/shared/none | test | hard:7 | 7 | 3 | 0.33333 | 0.10000 | 0.15385 | 0.42857 | 0 |
| semantic/shared/none | test | hard:8 | 19 | 2 | 1.00000 | 0.04878 | 0.09302 | 0.10526 | 0 |
| semantic/shared/none | test | hard:9 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| semantic/shared/none | tune | all | 34 | 3 | 1.00000 | 0.08824 | 0.16216 | 0.08824 | 0 |
| semantic/shared/none | tune | hard | 15 | 1 | 1.00000 | 0.05882 | 0.11111 | 0.06667 | 0 |
| semantic/shared/none | tune | hard:1 | 4 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| semantic/shared/none | tune | hard:2 | 4 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| semantic/shared/none | tune | hard:3 | 1 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| semantic/shared/none | tune | hard:4 | 6 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| semantic/shared/none | tune | hard:5 | 4 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| semantic/shared/none | tune | hard:6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| semantic/shared/none | tune | hard:7 | 3 | 1 | 1.00000 | 0.25000 | 0.40000 | 0.33333 | 0 |
| semantic/shared/none | tune | hard:8 | 2 | 0 | 0 | 0.00000 | 0 | 0.00000 | 0 |
| semantic/shared/none | tune | hard:9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Контрольные случаи (split указан, это не отдельная test-метрика)

| Вариант | Документ | Split | Case | Ожидание | Исход | Причины | Пройдено | Результат |
|---|---|---|---|---|---|---|---|---|
| combined/GigaChat 2 Lite/none | synth_extra/extra_001/doc_01 | tune | no_target | не рекомендация | review | reference_number_conflict | да | d0a0f353-ea1a-4cbd-9752-9c995ba54d35 |
| combined/GigaChat 2 Lite/none | synth_extra/extra_002/doc_01 | test | no_target | не рекомендация | review | reference_number_conflict | да | ce5d6875-de58-45e1-b109-7f8fa3a419ae |
| combined/GigaChat 2 Lite/none | synth_extra/extra_003/doc_01 | tune | no_target | не рекомендация | review | reference_number_conflict | да | 0d7990e3-f7d7-4cdf-900a-7cfd1c1039cd |
| combined/GigaChat 2 Lite/none | synth_extra/extra_004/doc_01 | test | no_target | не рекомендация | review | reference_number_unknown | да | b993fb91-1387-43de-b042-972139933c8f |
| combined/GigaChat 2 Lite/none | synth_extra/extra_005/doc_01 | test | no_target | не рекомендация | review | above_review_threshold | да | 4fe55b9b-b216-46b8-9e00-3689df871f20 |
| combined/GigaChat 2 Lite/none | synth_extra/extra_006/doc_01 | test | no_target | не рекомендация | none | below_review_threshold | да | c53f5ef8-a283-45c8-979e-0301f6e6c760 |
| combined/GigaChat 2 Lite/none | synth_extra/extra_007/doc_01 | test | multi3 | разбор | review | three_or_more_references, reference_number_unknown | да | c997b454-10c4-45e8-92f7-5244b6685b2e |
| combined/GigaChat 2 Lite/none | synth_extra/extra_008/doc_01 | test | multi3 | разбор | review | three_or_more_references, reference_number_unknown | да | 52e66999-9fff-44d6-a68f-58827fd6174d |
| combined/GigaChat 2 Lite/none | synth_extra/extra_009/doc_01 | test | multi3 | разбор | review | three_or_more_references, reference_number_unknown | да | 8b660b8f-2167-47ee-b7e3-c1ba0fdc35c3 |
| combined/GigaChat 2 Lite/none | synth_extra/extra_010/doc_01 | tune | conflict | разбор | review | reference_number_conflict | да | 3db1a613-c490-420e-af51-65065fd520ea |
| combined/GigaChat 2 Lite/none | synth_extra/extra_011/doc_01 | tune | conflict | разбор | review | reference_number_conflict | да | 28c19241-7c38-4531-82bf-434d1f250cc8 |
| combined/GigaChat 2 Lite/none | synth_extra/extra_012/doc_01 | test | conflict | разбор | review | reference_number_unknown | нет | b379a8d7-8c7c-4952-81f0-c662b259b04b |
| combined/GigaChat 2 Lite/none | synth_extra/extra_013/doc_01 | tune | conflict | разбор | review | reference_number_conflict | да | 1d990b05-221c-4b67-aa74-aff61396f258 |
| combined/GigaChat 2 Lite/none | synth_extra/extra_016/doc_01 | test | same_number | разбор | review | ambiguous_reference | да | 89f7ce4a-a491-460f-83b0-79ece7b1554d |
| combined/GigaChat 2 Lite/none | synth_extra/extra_019/doc_01 | test | same_number | разбор | review | ambiguous_reference | да | 9987437c-e805-4db9-b90a-7b3f60a7a23b |
| combined/Qwen/none | synth_extra/extra_001/doc_01 | tune | no_target | не рекомендация | review | reference_number_conflict | да | 33dac70b-500e-49e4-a6fb-46864fd2eeca |
| combined/Qwen/none | synth_extra/extra_002/doc_01 | test | no_target | не рекомендация | review | reference_number_conflict | да | 712837db-e978-4aad-80df-700235936c4b |
| combined/Qwen/none | synth_extra/extra_003/doc_01 | tune | no_target | не рекомендация | review | reference_number_conflict | да | f0677fb8-9039-4f59-adec-d8d065e95ace |
| combined/Qwen/none | synth_extra/extra_004/doc_01 | test | no_target | не рекомендация | review | reference_number_conflict | да | bba96dd2-ab43-4a9f-b543-e746b230899a |
| combined/Qwen/none | synth_extra/extra_005/doc_01 | test | no_target | не рекомендация | none | below_review_threshold | да | 71493ea9-034c-4139-9a62-88599f07df15 |
| combined/Qwen/none | synth_extra/extra_006/doc_01 | test | no_target | не рекомендация | none | below_review_threshold | да | 7baae96a-77a6-49c6-9673-c8340584566f |
| combined/Qwen/none | synth_extra/extra_007/doc_01 | test | multi3 | разбор | review | three_or_more_references, three_or_more_targets | да | c0d50912-2168-4c58-bea1-95838f3e4662 |
| combined/Qwen/none | synth_extra/extra_008/doc_01 | test | multi3 | разбор | review | three_or_more_references, three_or_more_targets | да | 68d17605-f4db-454d-a0ac-34e978cdcfc9 |
| combined/Qwen/none | synth_extra/extra_009/doc_01 | test | multi3 | разбор | review | three_or_more_references, three_or_more_targets | да | 6cfaa5f2-94aa-4568-abbc-86f7a8a2e566 |
| combined/Qwen/none | synth_extra/extra_010/doc_01 | tune | conflict | разбор | review | reference_number_conflict | да | a0a3318a-bded-4d5a-af07-af80b6b7618c |
| combined/Qwen/none | synth_extra/extra_011/doc_01 | tune | conflict | разбор | review | reference_number_conflict | да | ee5e6c5d-3ec1-4a8f-bd1e-e9370ba11a52 |
| combined/Qwen/none | synth_extra/extra_012/doc_01 | test | conflict | разбор | review | reference_number_conflict | да | 91d18955-c770-442f-a430-e1c87f0788c3 |
| combined/Qwen/none | synth_extra/extra_013/doc_01 | tune | conflict | разбор | review | reference_number_conflict | да | 37d1d02d-eb08-4b94-a5fd-c8858361bd91 |
| combined/Qwen/none | synth_extra/extra_016/doc_01 | test | same_number | разбор | review | ambiguous_reference | да | 11cca980-5365-4064-802c-261fb4ea6acd |
| combined/Qwen/none | synth_extra/extra_019/doc_01 | test | same_number | разбор | review | ambiguous_reference | да | 5362fa96-ce8b-4d1e-9580-9f114ea52130 |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_001/doc_01 | tune | no_target | не рекомендация | review | reference_number_conflict | да | 93af1cb6-c663-4245-9f61-7c5c0ad57510 |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_002/doc_01 | test | no_target | не рекомендация | review | reference_number_conflict | да | ac1ad687-a5ae-4521-8207-7894cc1425e6 |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_003/doc_01 | tune | no_target | не рекомендация | review | reference_number_conflict | да | 466c14ae-fd9f-4727-b717-b5fa609b7dbd |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_004/doc_01 | test | no_target | не рекомендация | review | reference_number_conflict | да | 9fae8ba8-fab9-488c-b0d8-ab8495f4b0d2 |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_005/doc_01 | test | no_target | не рекомендация | none | below_review_threshold | да | 991563fe-8cf9-4fa4-aaee-35c3620826be |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_006/doc_01 | test | no_target | не рекомендация | none | below_review_threshold | да | f1046fd1-8437-4b90-8ca6-a2b6dcb97fd6 |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_007/doc_01 | test | multi3 | разбор | review | three_or_more_references, three_or_more_targets | да | 83303495-b889-47be-8dcd-f258f834df31 |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_008/doc_01 | test | multi3 | разбор | review | three_or_more_references, three_or_more_targets | да | b32db76b-e692-40b5-8224-299d54b643da |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_009/doc_01 | test | multi3 | разбор | review | three_or_more_references, three_or_more_targets | да | 4058e6dc-2686-4a1d-9dd1-1f27040bfc8a |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_010/doc_01 | tune | conflict | разбор | review | reference_number_conflict | да | 7c266dec-6172-43fc-847e-71161ee68f8e |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_011/doc_01 | tune | conflict | разбор | review | reference_number_conflict | да | 1061208a-01b3-43c0-a50b-ded2e517ab08 |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_012/doc_01 | test | conflict | разбор | review | reference_number_conflict | да | 75409502-ca82-43e7-9f99-b38dd973ee80 |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_013/doc_01 | tune | conflict | разбор | review | reference_number_conflict | да | 455381ba-383a-4904-b60f-cc824cfc786d |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_016/doc_01 | test | same_number | разбор | review | ambiguous_reference | да | 3dc325ee-514f-4e63-b830-aa6ef629c69e |
| combined/YandexGPT 5.1 Pro/none | synth_extra/extra_019/doc_01 | test | same_number | разбор | review | ambiguous_reference | да | 1950fb59-3e04-4a4e-bfd0-5055e331ccd8 |

## Ресурсы и воспроизводимость

Новые физические LLM-вызовы: 0; новый API-расход: 0 ₽.
Стоимость уникальных исходных вызовов серии: 495.58761000 ₽; фактическое денежное списание не установлено.

| Вариант | reused_test | τ_a | τ_r | Переиспользовано исходов | мс link | Хеш протокола |
|---|---|---:|---:|---:|---:|---|
| combined/GigaChat 2 Lite/none | True | 0.75074 | 0.15717 | 153 | 169 | ca54a4f5564b900e7bab63c5cf9656e7769cbbb14611d9c5ec3136823f6874af |
| combined/Qwen/no_checks | True | 0.19620 | 0.18406 | 153 | 203 | 60b4f0e229a23ea8ab6f29cc4a894fb68b6ea637e9c1bb78d5cc37bb56cfaa5f |
| combined/Qwen/no_llm | True | — | — | — | — | 3b7f994c16080b2715f1fbfbdd2310338cdee6c251211df8f9ea8a210d67b091 |
| combined/Qwen/none | True | 0.19620 | 0.18406 | 153 | 184 | 2a3130c4a39a7ded5cb2a8bc1dc0ca885398fe36dcccd1334a6697d6245a07c6 |
| combined/Qwen/no_sem | True | 0.45500 | 0.45500 | 153 | 172 | 523ff5bdc3b1a1344606b056f4f4a1ec8cbb8aafd6b922f8432a06ff7f7ac953 |
| combined/YandexGPT 5.1 Pro/none | True | 0.19620 | 0.18406 | 153 | 244 | edfd2f47f2f673c585a8b9bcc4aef8683756c911a77f0200b743e3d5014df533 |
| rules/shared/none | True | 0.00000 | 0.00000 | 153 | 25 | feb5eb2be949df2019f389433655c14383f71d5b81ad476bacabcf32dc1e12bc |
| semantic/shared/none | True | 0.84094 | 0.71313 | 153 | 34 | 61ea2546e4bb5290832ad577074f9d0cc6617c1d73dea6358dc80137b6f3c659 |
