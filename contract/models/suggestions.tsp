namespace DocLink;

model LinkSuggestion {
  id: int64;
  run_id?: int64;
  source_id: uuid;
  target_id: uuid;
  link_type: LinkType;
  score: float64;
  features: Record<unknown>;
  penalties: Record<unknown>;
  rank: int32;
  decision: SuggestionDecision;
  created_at: utcDateTime;
}

model ReviewRequest {
  action: ReviewActionType;
  chosen_target_id?: uuid;
}

model ReviewAction {
  id: int64;
  suggestion_id?: int64;
  document_id: uuid;
  action: ReviewActionType;
  chosen_target_id?: uuid;
  opened_at: utcDateTime;
  decided_at: utcDateTime;
  duration_ms: int64;
}

model ReviewResult {
  review_action: ReviewAction;
  link?: Link;
}
