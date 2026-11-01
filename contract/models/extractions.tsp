namespace DocLink;

model ContractRef {
  number?: string;
  date?: plainDate;
  quote?: string;
}

model Evidence {
  number?: string;
  date?: string;
  counterparty?: string;
  amount?: string;
}

model Extraction {
  id: int64;
  document_id: uuid;
  model_config_id: int64;
  llm_call_id?: int64;
  doc_kind?: DocumentKind;
  number?: string;
  date?: plainDate;
  cp_name?: string;
  cp_inn?: string;
  amount?: string;
  subject?: string;
  contract_refs: ContractRef[];
  supp_ref?: ContractRef;
  invoice_ref?: ContractRef;
  evidence: Evidence;
  valid: boolean;
  evidence_ok: boolean;
  created_at: utcDateTime;
}
