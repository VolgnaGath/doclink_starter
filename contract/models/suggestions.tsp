namespace DocLink;

model Weights {
  num?: float64 = 0.45;
  date?: float64 = 0.15;
  inn?: float64 = 0.15;
  name?: float64 = 0.05;
  sem?: float64 = 0.2;
}

model LinkingConfig {
  method?: ExperimentMethod = ExperimentMethod.combined;
  tau_a: float64;
  tau_r: float64;
  weights?: Weights;
  amount_penalty?: float64 = 0.2;
  invalid_inn_penalty?: float64 = 0.05;
  checks?: boolean = true;
  use_semantic?: boolean = true;
  version?: "linking-v1" = "linking-v1";
}

model VerifiedReference {
  number?: string | null;
  date?: plainDate | null;
  verified?: boolean = false;
}

model Fields {
  number?: string | null;
  inn?: string | null;
  name?: string | null;
  amount?: string | float64 | null;
  references?: VerifiedReference[];
  number_verified?: boolean = false;
  counterparty_verified?: boolean = false;
  amount_verified?: boolean = false;
  error?: string | null;
}

model Penalty {
  reason: string;
  value: float64;
}

model Features {
  num: float64;
  date: float64;
  inn: float64;
  name: float64;
  sem: float64;
  reference_index?: int32 | null;
}

model LinkSuggestion {
  id: uuid;
  result_id: uuid;
  source_id: uuid;
  target_id: uuid;
  link_type: LinkType;
  features: Features;
  penalties: Penalty[];
  score: float64;
  rank: int32;
  decision: SuggestionDecision;
  source_evidence: Fields;
  target_evidence: Fields;
}

@doc("Исход целого документа. candidates может быть пустым, в том числе для none/error/review.")
model SearchOutcome {
  id: uuid;
  source_id: uuid;
  model_config_id: uuid | null;
  method: string;
  config: LinkingConfig;
  outcome: SuggestionFilter;
  reasons: string[];
  recommendations: uuid[];
  error: string | null;
  created_at: utcDateTime;
  input_hash: string;
  working_copy: CopyOrigin | null;
  candidates: LinkSuggestion[];
}
