namespace DocLink;

model ClientCreate {
  @minLength(1) @maxLength(300) name: string;
  @pattern("^(\\d{10}|\\d{12})$") inn?: string | null;
}

model Client {
  id: uuid;
  name: string;
  inn: string | null;
  `namespace`: string;
  created_at: utcDateTime;
}

model DocumentSummary {
  id: uuid;
  client_id: uuid;
  corpus_version_id: uuid | null;
  split: Split | null;
  source: DocumentSource;
  kind: DocumentKind | null;
  number: string | null;
  date: plainDate | null;
  filename: string;
  sha256: string;
  mime: string;
  card_model_config_id: uuid | null;
  card_extraction_id: uuid | null;
  is_anonymized: boolean;
  status: DocumentStatus;
  registered_at: utcDateTime;
  created_at: utcDateTime;
}

model DocumentText {
  text: string;
  pages: int32;
  chars: int32;
  ocr_pages: int32;
}

model Pipeline {
  document_id: uuid;
  batch_id: uuid;
  stage: string;
  job_id: uuid | null;
  result_id: uuid | null;
  error: string | null;
}

model DocumentDetail {
  document: DocumentSummary;
  text: DocumentText | null;
  extractions: Extraction[];
  result: SearchOutcome | null;
  links: Link[];
  review_state: ReviewState | null;
  review_actions: ReviewAction[];
  review_sessions: ReviewSession[];
  stage_timings: StageTiming[];
  pipeline: Pipeline | null;
  working_copy: CopyOrigin | null;
}

model UploadItem {
  filename: string;
  document_id: uuid | null;
  error: string | null;
}

@doc("Частичный приём: отдельный результат для каждого файла; общие ошибки отклоняют запрос целиком.")
model DocumentUploadResult {
  results: UploadItem[];
}

model ProcessRequest {
  mode?: "failed" | "search" | "extract" = "failed";
  model_id?: uuid | null;
}

model ProcessAccepted {
  document_id: uuid;
  job_ids: uuid[];
  pipeline: Pipeline | null;
}
