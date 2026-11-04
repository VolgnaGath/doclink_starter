# Схема БД DocLink

Сгенерировано из SQLAlchemy metadata (`make delivery-materials`). Только схема, без строк данных.

## `anon_identities`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `INTEGER` | нет | да |  |
| `client_id` | `CHAR(32)` | нет |  | clients.id |
| `kind` | `VARCHAR` | нет |  |  |
| `original` | `VARCHAR` | нет |  |  |
| `replacement` | `VARCHAR` | нет |  |  |

- `ForeignKeyConstraint`: `client_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `client_id`, `kind`, `original`
- `UniqueConstraint`: `client_id`, `replacement`

## `anon_maps`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `INTEGER` | нет | да |  |
| `document_id` | `CHAR(32)` | нет |  | documents.id |
| `kind` | `VARCHAR` | нет |  |  |
| `original` | `VARCHAR` | нет |  |  |
| `replacement` | `VARCHAR` | нет |  |  |

- `ForeignKeyConstraint`: `document_id`
- `PrimaryKeyConstraint`: `id`
- Индекс `ix_anon_maps_document_id`: anon_maps.document_id

## `card_edits`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `document_id` | `CHAR(32)` | нет |  | documents.id |
| `review_action_id` | `CHAR(32)` | нет |  | review_actions.id |
| `before` | `JSONB` | нет |  |  |
| `after` | `JSONB` | нет |  |  |
| `created_at` | `DATETIME` | нет |  |  |

- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `review_action_id`
- `PrimaryKeyConstraint`: `id`
- Индекс `ix_card_edits_document_id`: card_edits.document_id

## `clients`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `name` | `VARCHAR` | нет |  |  |
| `inn` | `VARCHAR` | да |  |  |
| `namespace` | `VARCHAR` | нет |  |  |
| `external_key` | `VARCHAR` | да |  |  |
| `created_at` | `DATETIME` | нет |  |  |

- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `namespace`, `external_key`

## `corpus_versions`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `name` | `VARCHAR` | нет |  |  |
| `fingerprint` | `VARCHAR` | нет |  |  |
| `imported_at` | `DATETIME` | нет |  |  |
| `seed` | `INTEGER` | нет |  |  |
| `tune_share` | `NUMERIC(8, 7)` | нет |  |  |
| `notes` | `VARCHAR` | да |  |  |
| `report` | `JSONB` | нет |  |  |

- `CheckConstraint`: `tune_share >= 0 AND tune_share <= 1`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `fingerprint`

## `counterparties`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `client_id` | `CHAR(32)` | нет |  | clients.id |
| `name` | `VARCHAR` | нет |  |  |
| `inn` | `VARCHAR` | да |  |  |
| `kpp` | `VARCHAR` | да |  |  |
| `aliases` | `JSONB` | нет |  |  |

- `ForeignKeyConstraint`: `client_id`
- `PrimaryKeyConstraint`: `id`
- Индекс `ix_counterparties_client_id`: counterparties.client_id

## `document_embeddings`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `document_id` | `CHAR(32)` | нет |  | documents.id |
| `embedding_id` | `CHAR(32)` | нет |  | embeddings.id |
| `original_text_hash` | `VARCHAR` | нет |  |  |
| `original_chars` | `INTEGER` | нет |  |  |
| `prepared_chars` | `INTEGER` | нет |  |  |
| `cache_hit` | `BOOLEAN` | нет |  |  |
| `created_at` | `DATETIME` | нет |  |  |

- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `embedding_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `document_id`, `embedding_id`, `original_text_hash`
- Индекс `ix_document_embeddings_document_id`: document_embeddings.document_id
- Индекс `ix_document_embeddings_embedding_id`: document_embeddings.embedding_id

## `document_texts`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `document_id` | `CHAR(32)` | нет | да | documents.id |
| `text` | `VARCHAR` | нет |  |  |
| `pages` | `INTEGER` | нет |  |  |
| `chars` | `INTEGER` | нет |  |  |
| `ocr_pages` | `INTEGER` | нет |  |  |
| `anonymized_text` | `VARCHAR` | да |  |  |

- `CheckConstraint`: `pages >= 0 AND chars >= 0 AND ocr_pages BETWEEN 0 AND pages`
- `ForeignKeyConstraint`: `document_id`
- `PrimaryKeyConstraint`: `document_id`

## `documents`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `client_id` | `CHAR(32)` | нет |  | clients.id |
| `corpus_version_id` | `CHAR(32)` | да |  | corpus_versions.id |
| `corpus_doc_id` | `VARCHAR` | да |  |  |
| `deal_id` | `VARCHAR` | да |  |  |
| `split` | `VARCHAR` | да |  |  |
| `source` | `VARCHAR` | нет |  |  |
| `filename` | `VARCHAR` | нет |  |  |
| `sha256` | `VARCHAR` | нет |  |  |
| `mime` | `VARCHAR` | нет |  |  |
| `storage_path` | `VARCHAR` | нет |  |  |
| `registered_at` | `DATETIME` | нет |  |  |
| `is_anonymized` | `BOOLEAN` | нет |  |  |
| `in_scope` | `BOOLEAN` | нет |  |  |
| `status` | `VARCHAR` | нет |  |  |
| `gold` | `JSONB` | да |  |  |
| `doc_kind` | `VARCHAR` | да |  |  |
| `number` | `VARCHAR` | да |  |  |
| `date` | `DATE` | да |  |  |
| `counterparty_id` | `CHAR(32)` | да |  | counterparties.id |
| `card_model_config_id` | `CHAR(32)` | да |  | model_configs.id |
| `card_extraction_id` | `CHAR(32)` | да |  | extractions.id |
| `created_at` | `DATETIME` | нет |  |  |

- `CheckConstraint`: `doc_kind IS NULL OR doc_kind IN ('договор','допсоглашение','счёт','акт','УПД')`
- `CheckConstraint`: `source IN ('upload', 'mock1c', 'corpus')`
- `CheckConstraint`: `split IS NULL OR split IN ('tune', 'test')`
- `CheckConstraint`: `status IN ('new','text','extracted','linked','review','manual','error')`
- `ForeignKeyConstraint`: `card_model_config_id`
- `ForeignKeyConstraint`: `client_id`
- `ForeignKeyConstraint`: `corpus_version_id`
- `ForeignKeyConstraint`: `counterparty_id`
- `ForeignKeyConstraint`: `card_extraction_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `corpus_version_id`, `corpus_doc_id`
- Индекс `ix_documents_client_registered`: documents.client_id, documents.registered_at
- Индекс `ix_documents_status`: documents.status

## `embeddings`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `input_hash` | `VARCHAR` | нет |  |  |
| `text_hash` | `VARCHAR` | нет |  |  |
| `model` | `VARCHAR` | нет |  |  |
| `revision` | `VARCHAR` | нет |  |  |
| `snapshot` | `JSONB` | нет |  |  |
| `vector` | `VECTOR(1024)` | нет |  |  |
| `tokens` | `INTEGER` | нет |  |  |
| `chunks` | `INTEGER` | нет |  |  |
| `truncated` | `BOOLEAN` | нет |  |  |
| `duration_ms` | `INTEGER` | нет |  |  |
| `created_at` | `DATETIME` | нет |  |  |

- `CheckConstraint`: `NOT truncated`
- `CheckConstraint`: `abs(vector_norm(vector) - 1) < 0.0001`
- `CheckConstraint`: `tokens > 0 AND chunks > 0 AND duration_ms >= 0`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `input_hash`
- Индекс `ix_embeddings_vector_hnsw`: embeddings.vector

## `experiment_artifacts`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `INTEGER` | нет | да |  |
| `run_id` | `CHAR(32)` | нет |  | experiment_runs.id |
| `extraction_id` | `CHAR(32)` | да |  | extractions.id |
| `llm_call_id` | `CHAR(32)` | да |  | llm_calls.id |
| `embedding_id` | `CHAR(32)` | да |  | embeddings.id |
| `timing_id` | `INTEGER` | да |  | stage_timings.id |

- `CheckConstraint`: `num_nonnulls(extraction_id,llm_call_id,embedding_id,timing_id) = 1`
- `ForeignKeyConstraint`: `embedding_id`
- `ForeignKeyConstraint`: `extraction_id`
- `ForeignKeyConstraint`: `llm_call_id`
- `ForeignKeyConstraint`: `run_id`
- `ForeignKeyConstraint`: `timing_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `run_id`, `embedding_id`
- `UniqueConstraint`: `run_id`, `extraction_id`
- `UniqueConstraint`: `run_id`, `llm_call_id`
- `UniqueConstraint`: `run_id`, `timing_id`
- Индекс `ix_experiment_artifacts_run_id`: experiment_artifacts.run_id

## `experiment_metrics`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `run_id` | `CHAR(32)` | нет | да | experiment_runs.id |
| `split` | `VARCHAR` | нет | да |  |
| `slice` | `VARCHAR` | нет | да |  |
| `metric` | `VARCHAR` | нет | да |  |
| `value` | `FLOAT` | да |  |  |
| `numerator` | `INTEGER` | да |  |  |
| `denominator` | `INTEGER` | да |  |  |

- `CheckConstraint`: `split IN ('tune','test')`
- `ForeignKeyConstraint`: `run_id`
- `PrimaryKeyConstraint`: `run_id`, `split`, `slice`, `metric`

## `experiment_results`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `run_id` | `CHAR(32)` | нет | да | experiment_runs.id |
| `document_id` | `CHAR(32)` | нет | да | documents.id |
| `result_id` | `CHAR(32)` | нет |  | linking_results.id |
| `timing_id` | `INTEGER` | нет |  | stage_timings.id |
| `reused` | `BOOLEAN` | нет |  |  |

- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `result_id`
- `ForeignKeyConstraint`: `run_id`
- `ForeignKeyConstraint`: `timing_id`
- `PrimaryKeyConstraint`: `run_id`, `document_id`

## `experiment_runs`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `series_id` | `CHAR(32)` | нет |  | experiment_series.id |
| `variant` | `VARCHAR` | нет |  |  |
| `model_config_id` | `CHAR(32)` | да |  | model_configs.id |
| `method` | `VARCHAR` | нет |  |  |
| `ablation` | `VARCHAR` | нет |  |  |
| `status` | `VARCHAR` | нет |  |  |
| `config` | `JSONB` | нет |  |  |
| `snapshot` | `JSONB` | нет |  |  |
| `protocol_hash` | `VARCHAR` | да |  |  |
| `reused_test` | `BOOLEAN` | нет |  |  |
| `test_started_at` | `DATETIME` | да |  |  |
| `started_at` | `DATETIME` | да |  |  |
| `finished_at` | `DATETIME` | да |  |  |
| `progress` | `INTEGER` | нет |  |  |
| `total` | `INTEGER` | нет |  |  |
| `error` | `VARCHAR` | да |  |  |
| `report` | `JSONB` | нет |  |  |

- `CheckConstraint`: `progress >= 0 AND total >= progress`
- `CheckConstraint`: `status IN ('queued','frozen','running','done','error','NO_FEASIBLE_CONFIGURATION','BLOCKED')`
- `ForeignKeyConstraint`: `model_config_id`
- `ForeignKeyConstraint`: `series_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `series_id`, `variant`
- Индекс `ix_experiment_runs_series_id`: experiment_runs.series_id

## `experiment_series`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `name` | `VARCHAR` | нет |  |  |
| `corpus_version_id` | `CHAR(32)` | нет |  | corpus_versions.id |
| `idempotency_key` | `CHAR(32)` | нет |  |  |
| `request_hash` | `VARCHAR` | нет |  |  |
| `request` | `JSONB` | нет |  |  |
| `status` | `VARCHAR` | нет |  |  |
| `job_id` | `CHAR(32)` | да |  | jobs.id |
| `selected_model_id` | `CHAR(32)` | да |  | model_configs.id |
| `error` | `VARCHAR` | да |  |  |
| `dataset` | `JSONB` | нет |  |  |
| `created_at` | `DATETIME` | нет |  |  |
| `finished_at` | `DATETIME` | да |  |  |

- `CheckConstraint`: `status IN ('queued','running','done','error')`
- `ForeignKeyConstraint`: `corpus_version_id`
- `ForeignKeyConstraint`: `job_id`
- `ForeignKeyConstraint`: `selected_model_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `idempotency_key`

## `extractions`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `document_id` | `CHAR(32)` | нет |  | documents.id |
| `model_config_id` | `CHAR(32)` | нет |  | model_configs.id |
| `llm_call_id` | `CHAR(32)` | да |  | llm_calls.id |
| `source_extraction_id` | `CHAR(32)` | да |  | extractions.id |
| `input_hash` | `VARCHAR` | да |  |  |
| `preserved_error` | `BOOLEAN` | нет |  |  |
| `doc_kind` | `VARCHAR` | да |  |  |
| `number` | `VARCHAR` | да |  |  |
| `date` | `DATE` | да |  |  |
| `cp_name` | `VARCHAR` | да |  |  |
| `cp_inn` | `VARCHAR` | да |  |  |
| `amount` | `NUMERIC(24, 4)` | да |  |  |
| `subject` | `VARCHAR` | да |  |  |
| `contract_refs` | `JSONB` | нет |  |  |
| `supp_ref` | `JSONB` | да |  |  |
| `invoice_ref` | `JSONB` | да |  |  |
| `evidence` | `JSONB` | нет |  |  |
| `evidence_checks` | `JSONB` | нет |  |  |
| `snapshot` | `JSONB` | нет |  |  |
| `valid` | `BOOLEAN` | нет |  |  |
| `evidence_ok` | `BOOLEAN` | нет |  |  |
| `cache_hit` | `BOOLEAN` | нет |  |  |
| `is_current` | `BOOLEAN` | нет |  |  |
| `error` | `VARCHAR` | да |  |  |
| `created_at` | `DATETIME` | нет |  |  |

- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `llm_call_id`
- `ForeignKeyConstraint`: `model_config_id`
- `ForeignKeyConstraint`: `source_extraction_id`
- `PrimaryKeyConstraint`: `id`
- Индекс `ix_extractions_document_model`: extractions.document_id, extractions.model_config_id
- Индекс `uq_extraction_current`: extractions.document_id, extractions.model_config_id

## `integration_attempts`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `INTEGER` | нет | да |  |
| `kind` | `VARCHAR` | нет |  |  |
| `reference` | `VARCHAR` | нет |  |  |
| `started_at` | `DATETIME` | нет |  |  |
| `duration_ms` | `INTEGER` | да |  |  |
| `status_code` | `INTEGER` | да |  |  |
| `error` | `VARCHAR` | да |  |  |

- `PrimaryKeyConstraint`: `id`

## `integration_events`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `link_id` | `CHAR(32)` | нет |  | links.id |
| `document_id` | `CHAR(32)` | нет |  | documents.id |
| `review_action_id` | `CHAR(32)` | нет |  | review_actions.id |
| `operation` | `VARCHAR` | нет |  |  |
| `payload` | `JSONB` | нет |  |  |
| `status` | `VARCHAR` | нет |  |  |
| `attempts` | `INTEGER` | нет |  |  |
| `created_at` | `DATETIME` | нет |  |  |
| `run_after` | `DATETIME` | нет |  |  |
| `delivered_at` | `DATETIME` | да |  |  |
| `error` | `VARCHAR` | да |  |  |

- `CheckConstraint`: `operation IN ('create','remove')`
- `CheckConstraint`: `status IN ('pending','done','error','blocked')`
- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `link_id`
- `ForeignKeyConstraint`: `review_action_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `link_id`, `operation`

## `integration_inbox`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `source` | `VARCHAR` | нет |  |  |
| `external_id` | `CHAR(32)` | нет |  |  |
| `metadata_snapshot` | `JSONB` | нет |  |  |
| `document_id` | `CHAR(32)` | да |  | documents.id |
| `status` | `VARCHAR` | нет |  |  |
| `attempts` | `INTEGER` | нет |  |  |
| `run_after` | `DATETIME` | нет |  |  |
| `error` | `VARCHAR` | да |  |  |

- `CheckConstraint`: `status IN ('pending','done','error','conflict')`
- `ForeignKeyConstraint`: `document_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `document_id`
- `UniqueConstraint`: `source`, `external_id`

## `integration_state`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `source` | `VARCHAR` | нет | да |  |
| `watermark` | `DATETIME` | да |  |  |
| `next_poll_at` | `DATETIME` | нет |  |  |
| `last_poll_at` | `DATETIME` | да |  |  |
| `error` | `VARCHAR` | да |  |  |

- `PrimaryKeyConstraint`: `source`

## `jobs`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `type` | `VARCHAR` | нет |  |  |
| `document_id` | `CHAR(32)` | нет |  | documents.id |
| `model_config_id` | `CHAR(32)` | да |  | model_configs.id |
| `payload` | `JSONB` | нет |  |  |
| `status` | `VARCHAR` | нет |  |  |
| `attempts` | `INTEGER` | нет |  |  |
| `run_after` | `DATETIME` | нет |  |  |
| `locked_at` | `DATETIME` | да |  |  |
| `error` | `VARCHAR` | да |  |  |

- `CheckConstraint`: `status IN ('queued','running','done','error')`
- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `model_config_id`
- `PrimaryKeyConstraint`: `id`
- Индекс `ix_jobs_status`: jobs.status
- Индекс `uq_job_active`: jobs.type, jobs.document_id, jobs.model_config_id

## `link_suggestions`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `result_id` | `CHAR(32)` | нет |  | linking_results.id |
| `source_id` | `CHAR(32)` | нет |  | documents.id |
| `target_id` | `CHAR(32)` | нет |  | documents.id |
| `link_type` | `VARCHAR` | нет |  |  |
| `features` | `JSONB` | нет |  |  |
| `penalties` | `JSONB` | нет |  |  |
| `score` | `FLOAT` | нет |  |  |
| `rank` | `INTEGER` | нет |  |  |
| `decision` | `VARCHAR` | нет |  |  |
| `source_evidence` | `JSONB` | нет |  |  |
| `target_evidence` | `JSONB` | нет |  |  |

- `CheckConstraint`: `decision IN ('recommendation','review','none')`
- `CheckConstraint`: `link_type IN ('изменение','основание','исполнение')`
- `CheckConstraint`: `rank > 0`
- `ForeignKeyConstraint`: `result_id`
- `ForeignKeyConstraint`: `source_id`
- `ForeignKeyConstraint`: `target_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `result_id`, `rank`
- `UniqueConstraint`: `result_id`, `target_id`
- Индекс `ix_link_suggestions_result_id`: link_suggestions.result_id

## `linking_results`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `source_id` | `CHAR(32)` | нет |  | documents.id |
| `model_config_id` | `CHAR(32)` | да |  | model_configs.id |
| `job_id` | `CHAR(32)` | да |  | jobs.id |
| `input_hash` | `VARCHAR` | нет |  |  |
| `method` | `VARCHAR` | нет |  |  |
| `method_version` | `VARCHAR` | нет |  |  |
| `config` | `JSONB` | нет |  |  |
| `input_snapshot` | `JSONB` | нет |  |  |
| `provenance` | `JSONB` | нет |  |  |
| `outcome` | `VARCHAR` | нет |  |  |
| `reasons` | `JSONB` | нет |  |  |
| `recommendations` | `JSONB` | нет |  |  |
| `error` | `VARCHAR` | да |  |  |
| `created_at` | `DATETIME` | нет |  |  |

- `CheckConstraint`: `method IN ('rules','semantic','combined')`
- `CheckConstraint`: `outcome IN ('recommendation','review','none','error')`
- `ForeignKeyConstraint`: `job_id`
- `ForeignKeyConstraint`: `model_config_id`
- `ForeignKeyConstraint`: `source_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `job_id`
- Индекс `ix_linking_results_input_hash`: linking_results.input_hash
- Индекс `ix_linking_results_source_id`: linking_results.source_id
- Индекс `uq_linking_success`: linking_results.input_hash

## `links`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `source_id` | `CHAR(32)` | нет |  | documents.id |
| `target_id` | `CHAR(32)` | нет |  | documents.id |
| `link_type` | `VARCHAR` | нет |  |  |
| `origin` | `VARCHAR` | нет |  |  |
| `review_action_id` | `CHAR(32)` | нет |  | review_actions.id |
| `suggestion_id` | `CHAR(32)` | да |  | link_suggestions.id |
| `created_at` | `DATETIME` | нет |  |  |
| `removed_at` | `DATETIME` | да |  |  |
| `removal_action_id` | `CHAR(32)` | да |  | review_actions.id |

- `CheckConstraint`: `link_type IN ('изменение','основание','исполнение')`
- `CheckConstraint`: `origin IN ('confirmed','manual')`
- `CheckConstraint`: `source_id <> target_id`
- `ForeignKeyConstraint`: `removal_action_id`
- `ForeignKeyConstraint`: `review_action_id`
- `ForeignKeyConstraint`: `source_id`
- `ForeignKeyConstraint`: `suggestion_id`
- `ForeignKeyConstraint`: `target_id`
- `PrimaryKeyConstraint`: `id`
- Индекс `ix_links_source_id`: links.source_id
- Индекс `ix_links_target_id`: links.target_id
- Индекс `uq_link_active`: links.source_id, links.target_id

## `llm_calls`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `model_config_id` | `CHAR(32)` | нет |  | model_configs.id |
| `prompt_id` | `CHAR(32)` | нет |  | prompts.id |
| `document_id` | `CHAR(32)` | да |  | documents.id |
| `job_id` | `CHAR(32)` | да |  | jobs.id |
| `purpose` | `VARCHAR` | нет |  |  |
| `input_hash` | `VARCHAR` | нет |  |  |
| `root_input_hash` | `VARCHAR` | нет |  |  |
| `snapshot` | `JSONB` | нет |  |  |
| `input_tokens` | `INTEGER` | да |  |  |
| `output_tokens` | `INTEGER` | да |  |  |
| `latency_ms` | `INTEGER` | да |  |  |
| `cost_rub` | `NUMERIC(18, 8)` | да |  |  |
| `reserved_rub` | `NUMERIC(18, 8)` | да |  |  |
| `paid` | `BOOLEAN` | нет |  |  |
| `status` | `VARCHAR` | нет |  |  |
| `error` | `VARCHAR` | да |  |  |
| `response_raw` | `VARCHAR` | да |  |  |
| `truncated` | `BOOLEAN` | нет |  |  |
| `reconciled` | `BOOLEAN` | нет |  |  |
| `started_at` | `DATETIME` | нет |  |  |
| `finished_at` | `DATETIME` | да |  |  |

- `CheckConstraint`: `cost_rub >= 0 AND reserved_rub >= 0`
- `CheckConstraint`: `input_tokens >= 0 AND output_tokens >= 0`
- `CheckConstraint`: `status IN ('started','success','invalid','error','uncertain')`
- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `job_id`
- `ForeignKeyConstraint`: `model_config_id`
- `ForeignKeyConstraint`: `prompt_id`
- `PrimaryKeyConstraint`: `id`
- Индекс `ix_llm_calls_document_id`: llm_calls.document_id
- Индекс `ix_llm_calls_root_input_hash`: llm_calls.root_input_hash
- Индекс `ix_llm_calls_status`: llm_calls.status
- Индекс `uq_llm_success`: llm_calls.input_hash, llm_calls.model_config_id, llm_calls.prompt_id

## `model_configs`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `name` | `VARCHAR` | нет |  |  |
| `provider` | `VARCHAR` | нет |  |  |
| `model_uri` | `VARCHAR` | нет |  |  |
| `placement` | `VARCHAR` | нет |  |  |
| `params` | `JSONB` | нет |  |  |
| `price_in_1k` | `NUMERIC(16, 8)` | да |  |  |
| `price_out_1k` | `NUMERIC(16, 8)` | да |  |  |
| `v_prefill` | `FLOAT` | да |  |  |
| `v_decode` | `FLOAT` | да |  |  |
| `active` | `BOOLEAN` | нет |  |  |
| `default_card` | `BOOLEAN` | нет |  |  |
| `created_at` | `DATETIME` | нет |  |  |

- `CheckConstraint`: `placement IN ('cloud','open-weights-cloud','local')`
- `CheckConstraint`: `price_in_1k >= 0 AND price_out_1k >= 0`
- `CheckConstraint`: `provider IN ('yandex','gigachat','ollama','regex')`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `name`

## `operational_config`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `INTEGER` | нет | да |  |
| `snapshot` | `JSONB` | нет |  |  |

- `CheckConstraint`: `id = 1`
- `PrimaryKeyConstraint`: `id`

## `processing_pipelines`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `document_id` | `CHAR(32)` | нет | да | documents.id |
| `batch_id` | `CHAR(32)` | нет |  |  |
| `snapshot` | `JSONB` | нет |  |  |
| `stage` | `VARCHAR` | нет |  |  |
| `job_id` | `CHAR(32)` | да |  | jobs.id |
| `error` | `VARCHAR` | да |  |  |
| `result_id` | `CHAR(32)` | да |  | linking_results.id |

- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `job_id`
- `ForeignKeyConstraint`: `result_id`
- `PrimaryKeyConstraint`: `document_id`
- Индекс `ix_processing_pipelines_batch_id`: processing_pipelines.batch_id

## `prompts`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `name` | `VARCHAR` | нет |  |  |
| `version` | `VARCHAR` | нет |  |  |
| `text` | `VARCHAR` | нет |  |  |
| `schema` | `JSONB` | нет |  |  |
| `created_at` | `DATETIME` | нет |  |  |

- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `name`, `version`

## `review_actions`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `document_id` | `CHAR(32)` | нет |  | documents.id |
| `session_id` | `CHAR(32)` | нет |  | review_sessions.id |
| `result_id` | `CHAR(32)` | да |  | linking_results.id |
| `suggestion_id` | `CHAR(32)` | да |  | link_suggestions.id |
| `chosen_target_id` | `CHAR(32)` | да |  | documents.id |
| `action` | `VARCHAR` | нет |  |  |
| `reason` | `VARCHAR` | да |  |  |
| `idempotency_key` | `CHAR(32)` | нет |  |  |
| `request_hash` | `VARCHAR` | нет |  |  |
| `opened_at` | `DATETIME` | нет |  |  |
| `decided_at` | `DATETIME` | нет |  |  |
| `duration_ms` | `INTEGER` | нет |  |  |
| `state_version` | `INTEGER` | нет |  |  |

- `CheckConstraint`: `action IN ('confirm','choose_other','reject','manual','complete','without_links','reopen','remove','edit')`
- `CheckConstraint`: `duration_ms >= 0`
- `ForeignKeyConstraint`: `chosen_target_id`
- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `result_id`
- `ForeignKeyConstraint`: `session_id`
- `ForeignKeyConstraint`: `suggestion_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `document_id`, `idempotency_key`
- Индекс `ix_review_actions_document_id`: review_actions.document_id

## `review_sessions`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `document_id` | `CHAR(32)` | нет |  | documents.id |
| `result_id` | `CHAR(32)` | да |  | linking_results.id |
| `context` | `JSONB` | нет |  |  |
| `opened_at` | `DATETIME` | нет |  |  |
| `completed_at` | `DATETIME` | да |  |  |
| `duration_ms` | `INTEGER` | да |  |  |

- `CheckConstraint`: `duration_ms IS NULL OR duration_ms >= 0`
- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `result_id`
- `PrimaryKeyConstraint`: `id`
- Индекс `ix_review_sessions_document_id`: review_sessions.document_id

## `review_states`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `document_id` | `CHAR(32)` | нет | да | documents.id |
| `version` | `INTEGER` | нет |  |  |
| `status` | `VARCHAR` | нет |  |  |
| `overrides` | `JSONB` | нет |  |  |
| `needs_review` | `BOOLEAN` | нет |  |  |

- `CheckConstraint`: `status IN ('open','completed')`
- `CheckConstraint`: `version >= 0`
- `ForeignKeyConstraint`: `document_id`
- `PrimaryKeyConstraint`: `document_id`

## `stage_timings`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `INTEGER` | нет | да |  |
| `document_id` | `CHAR(32)` | нет |  | documents.id |
| `stage` | `VARCHAR` | нет |  |  |
| `duration_ms` | `INTEGER` | нет |  |  |
| `ok` | `BOOLEAN` | нет |  |  |
| `error` | `VARCHAR` | да |  |  |
| `started_at` | `DATETIME` | нет |  |  |

- `CheckConstraint`: `duration_ms >= 0`
- `ForeignKeyConstraint`: `document_id`
- `PrimaryKeyConstraint`: `id`
- Индекс `ix_stage_timings_document_id`: stage_timings.document_id

## `threshold_curves`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `run_id` | `CHAR(32)` | нет | да | experiment_runs.id |
| `index` | `INTEGER` | нет | да |  |
| `split` | `VARCHAR` | нет |  |  |
| `purpose` | `VARCHAR` | нет |  |  |
| `values` | `JSONB` | нет |  |  |

- `CheckConstraint`: `split = 'tune' AND purpose = 'tuning'`
- `ForeignKeyConstraint`: `run_id`
- `PrimaryKeyConstraint`: `run_id`, `index`

## `working_copies`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `document_id` | `CHAR(32)` | нет | да | documents.id |
| `working_set_id` | `CHAR(32)` | нет |  | working_sets.id |
| `source_document_id` | `CHAR(32)` | нет |  | documents.id |
| `source_result_id` | `CHAR(32)` | да |  | linking_results.id |
| `source_result_current` | `BOOLEAN` | нет |  |  |

- `ForeignKeyConstraint`: `document_id`
- `ForeignKeyConstraint`: `source_document_id`
- `ForeignKeyConstraint`: `source_result_id`
- `ForeignKeyConstraint`: `working_set_id`
- `PrimaryKeyConstraint`: `document_id`
- `UniqueConstraint`: `working_set_id`, `source_document_id`
- Индекс `ix_working_copies_working_set_id`: working_copies.working_set_id

## `working_sets`

| Поле | Тип | NULL | PK | Ссылка |
|---|---|---|---|---|
| `id` | `CHAR(32)` | нет | да |  |
| `corpus_version_id` | `CHAR(32)` | нет |  | corpus_versions.id |
| `name` | `VARCHAR` | нет |  |  |
| `idempotency_key` | `CHAR(32)` | нет |  |  |
| `request_hash` | `VARCHAR` | нет |  |  |
| `baseline` | `JSONB` | нет |  |  |
| `created_at` | `DATETIME` | нет |  |  |

- `ForeignKeyConstraint`: `corpus_version_id`
- `PrimaryKeyConstraint`: `id`
- `UniqueConstraint`: `idempotency_key`
