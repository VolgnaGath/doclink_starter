namespace DocLink;

model ReviewRequest {
  session_id: uuid;
  @minValue(0) version: int32;
  idempotency_key: uuid;
  action: ReviewActionType;
  suggestion_id?: uuid | null;
  target_id?: uuid | null;
  link_id?: uuid | null;
  @maxLength(2000) reason?: string | null;
}

model CardPatch {
  session_id: uuid;
  @minValue(0) version: int32;
  idempotency_key: uuid;

  @doc("doc_kind, number, date (ДД.ММ.ГГГГ), counterparty_name/inn, amount, subject, contract_refs, supp_ref, invoice_ref; null — явная очистка.")
  values?: Record<unknown>;

  reset?: string[];
}

model ReviewState {
  document_id: uuid;
  version: int32;
  status: "open" | "completed";
  overrides: Record<unknown>;
  needs_review: boolean;
}

model ReviewSession {
  id: uuid;
  document_id: uuid;
  result_id: uuid | null;
  opened_at: utcDateTime;
  completed_at: utcDateTime | null;
  duration_ms: int64 | null;
  input_revision: string;
  working_copy: CopyOrigin | null;
}

model ReviewAction {
  id: uuid;
  document_id: uuid;
  session_id: uuid;
  result_id: uuid | null;
  suggestion_id: uuid | null;
  chosen_target_id: uuid | null;
  action: string;
  reason: string | null;
  idempotency_key: uuid;
  opened_at: utcDateTime;
  decided_at: utcDateTime;
  duration_ms: int64;
  state_version: int32;
}
