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
  id: int64;
  name: string;
  method: ExperimentMethod;
  model_config_id?: uuid;
  embedder: string;
  weights: Record<unknown>;
  tau_a?: float64;
  tau_r?: float64;
  ablation: Ablation;
  corpus_version_id: uuid;
  split_seed: int64;
  git_commit: string;
  status: RunStatus;
  started_at?: utcDateTime;
  finished_at?: utcDateTime;
  cost_rub?: string;
  notes?: string;
}

model ExperimentMetric {
  run_id: int64;
  slice: string;
  metric: string;
  value: float64;
}

model ThresholdCurvePoint {
  tau: float64;
  precision: float64;
  recall: float64;
  recommendation_share: float64;
}

model ExperimentDetail {
  run: ExperimentRun;
  model_config?: ModelConfig;
  metrics: ExperimentMetric[];
  threshold_curve: ThresholdCurvePoint[];
}

model ExperimentCreate {
  name: string;
  method: ExperimentMethod;
  model_config_id?: uuid;
  embedder: string;
  weights: Record<unknown>;
  tau_a?: float64;
  tau_r?: float64;
  ablation: Ablation;
  corpus_version_id: uuid;
  split_seed: int64;
}

model ExperimentAccepted {
  @statusCode statusCode: 202;
  @bodyRoot body: ExperimentRun;
}

model ExportFile extends File<
  "text/csv" | "text/markdown" | "image/png",
  bytes
> {}
