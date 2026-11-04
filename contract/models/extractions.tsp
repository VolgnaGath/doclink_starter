namespace DocLink;

@doc("Даты внутри извлечений/ручных ссылок — ДД.ММ.ГГГГ; дата самого Extraction — ISO plainDate.")
model ContractRef {
  number?: string | null;
  date?: string | null;
  quote?: string | null;
}

model EvidenceCheck {
  field: string;
  ok: boolean;
  quote?: string | null;
  original_quote?: string | null;
  method?: string | null;
  start?: int32 | null;
  end?: int32 | null;
}

model Extraction {
  id: uuid;
  document_id: uuid;
  model_config_id: uuid;
  llm_call_id: uuid | null;
  source_extraction_id: uuid | null;
  doc_kind: DocumentKind | null;
  number: string | null;
  date: plainDate | null;
  cp_name: string | null;
  cp_inn: string | null;
  amount: string | null;
  subject: string | null;
  contract_refs: ContractRef[];
  supp_ref: ContractRef | null;
  invoice_ref: ContractRef | null;
  evidence: Record<string | null>;
  evidence_checks: EvidenceCheck[];
  valid: boolean;
  evidence_ok: boolean;
  cache_hit: boolean;
  is_current: boolean;
  preserved_error: boolean;
  error: string | null;
  created_at: utcDateTime;
}
