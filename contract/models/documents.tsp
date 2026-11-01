using TypeSpec.Http;

namespace DocLink;

model Client {
  id: uuid;
  name: string;
  inn?: string;
  created_at: utcDateTime;
}

model Counterparty {
  id: int64;
  client_id: uuid;
  name: string;
  inn?: string;
  kpp?: string;
  aliases?: string[];
}

model DocumentSummary {
  id: uuid;
  client_id: uuid;
  corpus_version_id?: int64;
  corpus_doc_id?: string;
  deal_id?: string;
  split?: Split;
  source: DocumentSource;
  kind?: DocumentKind;
  number?: string;
  date?: plainDate;
  filename: string;
  sha256: string;
  mime: string;
  registered_at: utcDateTime;
  is_anonymized: boolean;
  in_scope: boolean;
  status: DocumentStatus;
  created_at: utcDateTime;
}

model DocumentText {
  pages: int32;
  chars: int32;
  ocr_pages: int32;
  text: string;
  anonymized_text?: string;
}

model DocumentDetail {
  document: DocumentSummary;
  text?: DocumentText;
  extractions: Extraction[];
  suggestions: LinkSuggestion[];
  links: Link[];
  stage_timings: StageTiming[];
}

model DocumentUploadResult {
  documents: DocumentSummary[];
}

model DocumentUploadCreated {
  @statusCode statusCode: 201;
  @bodyRoot body: DocumentUploadResult;
}

model JobAccepted {
  @statusCode statusCode: 202;
  @bodyRoot body: JobRef;
}
