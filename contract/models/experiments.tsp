using TypeSpec.Http;

namespace DocLink;

model ModelConfig {
  id: uuid;
  name: string;
  provider: Provider;
  model_uri: string;
  placement: Placement;
  params: Record<unknown>;
  price_in_1k?: string;
  price_out_1k?: string;
  v_prefill?: float64;
  v_decode?: float64;
  active: boolean;
}

model ExperimentRun {
  id: uuid;
  variant: string;
  method: ExperimentMethod;
  model_config_id: uuid | null;
  ablation: Ablation;
  status: RunStatus | "frozen" | "NO_FEASIBLE_CONFIGURATION" | "BLOCKED";
  progress: int64;
  total: int64;
  config: Record<unknown>;
  snapshot: Record<unknown>;
  protocol_hash: string | null;
  reused_test: boolean;
  test_started_at: utcDateTime | null;
  started_at: utcDateTime | null;
  finished_at: utcDateTime | null;
  error: string | null;
  report: Record<unknown>;
  metrics: ExperimentMetric[];
  threshold_curves: ThresholdCurvePoint[];
}

model ExperimentMetric {
  split: Split;
  slice: string;
  metric: string;
  value: float64 | null;
  numerator: int64 | null;
  denominator: int64 | null;
}

model ThresholdCurvePoint {
  split: "tune";
  purpose: "tuning";
  values: Record<unknown>;
}

model ExperimentDetail {
  id: uuid;
  name: string;
  corpus_version_id: uuid;
  status: RunStatus;
  mode: "matrix" | "single" | "ablations" | "holdout";
  selected_model_id: uuid | null;
  progress: int64;
  total: int64;
  error: string | null;
  created_at: utcDateTime;
  finished_at: utcDateTime | null;
  dataset_hash: string | null;
  runs: ExperimentRun[];
}

model ExperimentCreate {
  @minLength(1) @maxLength(200) name: string;
  corpus_version_id: uuid;
  idempotency_key: uuid;
  mode?: "matrix" | "single" | "ablations" = "matrix";
  method?: ExperimentMethod = ExperimentMethod.combined;
  model_config_id?: uuid;
  ablation?: Ablation = Ablation.none;
}

model ExperimentAccepted {
  @statusCode statusCode: 202;
  @bodyRoot body: ExperimentDetail;
}

model ExportFile extends File<
  "text/csv" | "text/markdown" | "image/png",
  bytes
> {}
