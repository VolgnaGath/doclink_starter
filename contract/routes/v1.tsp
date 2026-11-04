using TypeSpec.Http;

namespace DocLink {
  alias ApiErrors =
    | UnauthorizedResponse
    | NotFoundResponse
    | ConflictResponse
    | UnprocessableEntityResponse
    | InternalServerErrorResponse;

  @route("/health")
  @get
  op health(): HealthStatus | InternalServerErrorResponse;

  @route("/api/v1")
  @useAuth(ApiKeyAuth<ApiKeyLocation.cookie, "session">)
  namespace V1 {
    @get
    @route("/clients")
    op listClients(
      @query working_only?: boolean = true,

      @query
      @minValue(1)
      @maxValue(100)
      limit?: int32 = 50,

      @query @minValue(0) offset?: int32 = 0,
    ): Page<Client> | ApiErrors;

    @post
    @route("/clients")
    op createClient(@body body: ClientCreate): Created<Client> | ApiErrors;

    @post
    @route("/documents")
    op uploadDocuments(
      @multipartBody body: {
        client_id: HttpPart<uuid>;
        files: HttpPart<File>[];
        is_anonymized?: HttpPart<boolean>;
      },
    ): Created<DocumentUploadResult> | ApiErrors;

    @get
    @route("/documents")
    op listDocuments(
      @query client_id?: uuid,
      @query kind?: DocumentKind,
      @query status?: DocumentStatus,
      @query split?: Split,
      @query source?: DocumentSource,
      @query corpus_version_id?: uuid,

      @query
      @minValue(1)
      @maxValue(100)
      limit?: int32 = 50,

      @query @minValue(0) offset?: int32 = 0,
    ): Page<DocumentSummary> | ApiErrors;

    @get
    @route("/documents/{id}")
    op getDocument(@path id: uuid): DocumentDetail | ApiErrors;

    @post
    @route("/documents/{id}/process")
    op processDocument(@path id: uuid, @body body?: ProcessRequest):
      | Accepted<ProcessAccepted>
      | ApiErrors;

    @patch
    @route("/documents/{id}/card")
    op editCard(@path id: uuid, @body body: CardPatch): ReviewState | ApiErrors;

    @post
    @route("/documents/{id}/review-sessions")
    op openReview(@path id: uuid): Created<ReviewSession> | ApiErrors;

    @doc("Решения без обязательного suggestion_id, включая complete/without_links/reopen/remove. Серверный таймер, версия вкладки и ключ идемпотентности обязательны.")
    @post
    @route("/documents/{id}/review")
    op reviewDocument(@path id: uuid, @body body: ReviewRequest):
      | ReviewAction
      | ApiErrors;

    @get
    @route("/suggestions")
    op listSuggestions(
      @query decision?: SuggestionFilter,
      @query document_id?: uuid,
      @query client_id?: uuid,
      @query history?: boolean = false,

      @query
      @minValue(1)
      @maxValue(100)
      limit?: int32 = 50,

      @query @minValue(0) offset?: int32 = 0,
    ): Page<SearchOutcome> | ApiErrors;

    @post
    @route("/suggestions/{id}/review")
    op reviewSuggestion(@path id: uuid, @body body: ReviewRequest):
      | ReviewAction
      | ApiErrors;

    @get
    @route("/links")
    op listLinks(
      @query document_id?: uuid,
      @query client_id?: uuid,
      @query link_type?: LinkType,
      @query origin?: LinkOrigin,
      @query include_removed?: boolean = false,

      @query
      @minValue(1)
      @maxValue(100)
      limit?: int32 = 50,

      @query @minValue(0) offset?: int32 = 0,
    ): Page<Link> | ApiErrors;

    @get
    @route("/corpus")
    op listCorpus(
      @query
      @minValue(1)
      @maxValue(100)
      limit?: int32 = 50,

      @query @minValue(0) offset?: int32 = 0,
    ): Page<CorpusVersion> | ApiErrors;

    @post
    @route("/corpus/import")
    op importCorpus(
      @multipartBody body: {
        archive: HttpPart<File>;
        seed?: HttpPart<int64>;
        tune_share?: HttpPart<float64>;
      },
    ): CorpusImportResult | ApiErrors;

    @post
    @route("/working-sets")
    op createWorkingSet(@body body: WorkingSetCreate):
      | Created<WorkingSetDetail>
      | ApiErrors;

    @get
    @route("/working-sets")
    op listWorkingSets(
      @query
      @minValue(1)
      @maxValue(100)
      limit?: int32 = 50,

      @query @minValue(0) offset?: int32 = 0,
    ): Page<WorkingSetSummary> | ApiErrors;

    @get
    @route("/working-sets/{id}")
    op getWorkingSet(@path id: uuid): WorkingSetDetail | ApiErrors;

    @doc("Создать фоновую серию: матрица, tune-выбор модели с абляциями или один прогон. Ключ идемпотентности не повторяет вычисления.")
    @post
    @route("/experiments")
    op createExperiment(@body body: ExperimentCreate):
      | ExperimentAccepted
      | ApiErrors;

    @doc("Прогресс серии, freeze и происхождение, раздельные tune/test-метрики и кривые настройки.")
    @get
    @route("/experiments/{id}")
    op getExperiment(@path id: uuid): ExperimentDetail | ApiErrors;

    @doc("Выгрузка из одного сохранённого dataset: таблицы 2.6–2.8 CSV/Markdown, рисунок 2.4 PNG (только tune). До завершения — 409.")
    @get
    @route("/experiments/{id}/export")
    op exportExperiment(@path id: uuid, @query format: ExportFormat):
      | ExportFile
      | ApiErrors;
  }
}
