using TypeSpec.Http;

namespace DocLink;

@format("uuid")
scalar uuid extends string;

enum DocumentStatus {
  new_doc: "new",
  text: "text",
  extracted: "extracted",
  linked: "linked",
  review: "review",
  manual: "manual",
  error: "error",
}

enum DocumentSource {
  upload: "upload",
  mock1c: "mock1c",
  corpus: "corpus",
}

enum Split {
  tune: "tune",
  test: "test",
}

enum DocumentKind {
  dogovor: "договор",
  dop_soglashenie: "допсоглашение",
  schet: "счёт",
  akt: "акт",
  upd: "УПД",
  drugoe: "другое",
}

enum LinkType {
  izmenenie: "изменение",
  osnovanie: "основание",
  ispolnenie: "исполнение",
}

enum SuggestionDecision {
  recommendation: "recommendation",
  review: "review",
  none: "none",
}

enum SuggestionFilter {
  recommendation: "recommendation",
  review: "review",
  none: "none",
  error: "error",
}

enum LinkOrigin {
  confirmed: "confirmed",
  manual: "manual",
}

enum ReviewActionType {
  confirm: "confirm",
  choose_other: "choose_other",
  reject: "reject",
  manual: "manual",
  complete: "complete",
  without_links: "without_links",
  reopen: "reopen",
  remove: "remove",
}

enum ExperimentMethod {
  rules: "rules",
  semantic: "semantic",
  combined: "combined",
}

enum Ablation {
  none: "none",
  no_llm: "no_llm",
  no_checks: "no_checks",
  no_sem: "no_sem",
}

enum Provider {
  yandex: "yandex",
  gigachat: "gigachat",
  ollama: "ollama",
  regex: "regex",
}

enum Placement {
  cloud: "cloud",
  open_weights_cloud: "open-weights-cloud",
  local: "local",
}

enum RunStatus {
  queued: "queued",
  running: "running",
  done: "done",
  error: "error",
}

enum JobStatus {
  queued: "queued",
  running: "running",
  done: "done",
  error: "error",
}

enum ExportFormat {
  csv: "csv",
  md: "md",
  png: "png",
}

enum ProcessingStage {
  text: "text",
  anonymize: "anonymize",
  extract: "extract",
  embed: "embed",
  link: "link",
}

model Page<T> {
  items: T[];
  total: int64;
  limit: int32;
  offset: int32;
}

model ErrorBody {
  code: string;
  message: string;
  details: Record<unknown>[];
}

@error
model BadRequestResponse extends ErrorBody {
  @statusCode statusCode: 400;
}

@error
model UnauthorizedResponse extends ErrorBody {
  @statusCode statusCode: 401;
}

@error
model NotFoundResponse extends ErrorBody {
  @statusCode statusCode: 404;
}

@error
model ConflictResponse extends ErrorBody {
  @statusCode statusCode: 409;
}

@error
model UnprocessableEntityResponse extends ErrorBody {
  @statusCode statusCode: 422;
}

@error
model InternalServerErrorResponse extends ErrorBody {
  @statusCode statusCode: 500;
}

model HealthStatus {
  status: "ok" | "degraded";
  db: boolean;
  version: string;
}

model JobRef {
  job_id: uuid;
  status: JobStatus;
}

model StageTiming {
  id: int64;
  stage: string;
  duration_ms: int64;
  ok: boolean;
  error: string | null;
  started_at: utcDateTime;
}

model Created<T> {
  @statusCode statusCode: 201;
  @bodyRoot body: T;
}

model Accepted<T> {
  @statusCode statusCode: 202;
  @bodyRoot body: T;
}
