namespace DocLink;

model CorpusImportResult {
  corpus_version_id: uuid;
  reused: boolean;
  documents_imported: int32;
  links_imported: int32;
}

model CorpusVersion {
  id: uuid;
  name: string;
  imported_at: utcDateTime;
}

model WorkingSetCreate {
  @minLength(1) @maxLength(200) name: string;
  corpus_version_id: uuid;
  @minItems(1) @maxItems(1000) document_ids: uuid[];
  @maxItems(1000) result_ids?: uuid[];
  idempotency_key: uuid;
}

model CopyOrigin {
  working_set_id: uuid;
  source_document_id: uuid;
  source_result_id: uuid | null;
  initial_inputs: boolean;
}

model Copy {
  document_id: uuid;
  source_document_id: uuid;
  source_result_id: uuid | null;
  client_id: uuid;
}

model WorkingSetSummary {
  id: uuid;
  name: string;
  corpus_version_id: uuid;
  created_at: utcDateTime;
}

model WorkingSetDetail extends WorkingSetSummary {
  copies: Copy[];
}
