# Живая приёмка связывания — этап 4.6

Протокол: `7592e5fee99eb9d9a3232727100a72a31038aea9f099383f514aa73a3a0b369f`.
reused_test=true. Пороги выбраны только на tune; контрольная таблица не является отдельной test-метрикой.

| Часть | N | M | Precision | Recall | Охват | Ошибок обработки |
|---|---:|---:|---:|---:|---:|---:|
| tune | 34 | 28 | 1.0000 | 0.8235 | 0.8235 | 0 |
| test | 119 | 95 | 0.9263 | 0.6423 | 0.7815 | 0 |

Контрольные случаи: **15/15**.

| Документ | Split | Case | Ожидание | Исход | Причины | Кандидаты (top-3; полный рейтинг в JSON) | Пройдено |
|---|---|---|---|---|---|---|---|
| synth_extra/extra_001/doc_01 | tune | no_target | не рекомендация | review | reference_number_conflict | synth/synth_x036/doc_01 (0.1624); synth/synth_x037/doc_01 (0.1573); synth/synth_x040/doc_01 (0.1477) | да |
| synth_extra/extra_002/doc_01 | test | no_target | не рекомендация | review | reference_number_conflict | synth/synth_010/doc_07 (0.1871); synth/synth_010/doc_01 (0.1830); synth/synth_x031/doc_01 (0.1381) | да |
| synth_extra/extra_003/doc_01 | tune | no_target | не рекомендация | review | reference_number_conflict | synth/synth_024/doc_01 (0.1498); synth/synth_014/doc_01 (0.1498); synth/synth_019/doc_01 (0.1496) | да |
| synth_extra/extra_004/doc_01 | test | no_target | не рекомендация | review | reference_number_conflict | synth/synth_010/doc_01 (0.1731); synth/synth_010/doc_07 (0.1730); synth/synth_014/doc_01 (0.1298) | да |
| synth_extra/extra_005/doc_01 | test | no_target | не рекомендация | none | below_review_threshold | synth/synth_x021/doc_01 (0.1639); synth/synth_x019/doc_01 (0.1606); synth/synth_x024/doc_01 (0.1581) | да |
| synth_extra/extra_006/doc_01 | test | no_target | не рекомендация | none | below_review_threshold | synth/synth_005/doc_01 (0.1487); synth/synth_012/doc_01 (0.1455); synth/synth_012/doc_06 (0.1442) | да |
| synth_extra/extra_007/doc_01 | test | multi3 | разбор | review | three_or_more_references, three_or_more_targets | synth/synth_x039/doc_01 (0.9241); synth/synth_x037/doc_01 (0.7618); synth/synth_x035/doc_01 (0.4122) | да |
| synth_extra/extra_008/doc_01 | test | multi3 | разбор | review | three_or_more_references, three_or_more_targets | synth/synth_x007/doc_01 (0.9676); synth/synth_x005/doc_01 (0.9630); synth/synth_012/doc_06 (0.7426) | да |
| synth_extra/extra_009/doc_01 | test | multi3 | разбор | review | three_or_more_references, three_or_more_targets | synth/synth_006/doc_01 (0.9381); synth/synth_x018/doc_01 (0.8093); synth/synth_017/doc_01 (0.7503) | да |
| synth_extra/extra_010/doc_01 | tune | conflict | разбор | review | reference_number_conflict | synth/synth_x048/doc_01 (0.3304); synth/synth_x047/doc_01 (0.3175); synth_extra/extra_014/doc_01 (0.1968) | да |
| synth_extra/extra_011/doc_01 | tune | conflict | разбор | review | reference_number_conflict | synth/synth_019/doc_01 (0.3610); synth/synth_x026/doc_01 (0.3387); synth/synth_019/doc_07 (0.3380) | да |
| synth_extra/extra_012/doc_01 | test | conflict | разбор | review | reference_number_conflict | synth/synth_x032/doc_01 (0.3583); synth/synth_x026/doc_01 (0.1339); synth/synth_x025/doc_01 (0.1291) | да |
| synth_extra/extra_013/doc_01 | tune | conflict | разбор | review | reference_number_conflict | synth/synth_019/doc_01 (0.3608); synth/synth_x026/doc_01 (0.3365); synth/synth_019/doc_07 (0.3353) | да |
| synth_extra/extra_016/doc_01 | test | same_number | разбор | review | ambiguous_reference | synth_extra/extra_014/doc_01 (0.8157); synth_extra/extra_015/doc_01 (0.8155); synth/synth_x048/doc_01 (0.1617) | да |
| synth_extra/extra_019/doc_01 | test | same_number | разбор | review | ambiguous_reference | synth_extra/extra_018/doc_01 (0.8126); synth_extra/extra_017/doc_01 (0.8112); synth/synth_x036/doc_01 (0.1598) | да |

Новые физические LLM-вызовы: 0.
Техническая приёмка выбранной конфигурации; полная матрица и итоговая проверка — этап 6.
