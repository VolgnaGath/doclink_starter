using TypeSpec.Http;

namespace DocLink {
  @route("/health")
  @get
  op health(): HealthStatus | InternalServerErrorResponse;

  @route("/api/v1")
  @useAuth(ApiKeyAuth<ApiKeyLocation.cookie, "session">)
  namespace V1 {
    @post
    @route("/documents")
    op uploadDocuments(
      @multipartBody body: {
        client_id: HttpPart<uuid>;
        files: HttpPart<File>[];
        is_anonymized?: HttpPart<boolean>;
      },
    ):
      | DocumentUploadCreated
      | BadRequestResponse
      | UnauthorizedResponse
      | InternalServerErrorResponse;

    @get
    @route("/documents")
    op listDocuments(
      @query client_id?: uuid,
      @query kind?: DocumentKind,
      @query status?: DocumentStatus,
      @query split?: Split,
      @query source?: DocumentSource,
      @query limit?: int32 = 50,
      @query offset?: int32 = 0,
    ):
      | Page<DocumentSummary>
      | UnauthorizedResponse
      | InternalServerErrorResponse;

    @get
    @route("/documents/{id}")
    op getDocument(@path id: uuid):
      | DocumentDetail
      | UnauthorizedResponse
      | NotFoundResponse
      | InternalServerErrorResponse;

    @post
    @route("/documents/{id}/process")
    op processDocument(@path id: uuid):
      | JobAccepted
      | UnauthorizedResponse
      | NotFoundResponse
      | ConflictResponse
      | InternalServerErrorResponse;

    @get
    @route("/suggestions")
    op listSuggestions(
      @query decision: SuggestionFilter,
      @query document_id?: uuid,
      @query run_id?: int64,
      @query limit?: int32 = 50,
      @query offset?: int32 = 0,
    ):
      | Page<LinkSuggestion>
      | BadRequestResponse
      | UnauthorizedResponse
      | InternalServerErrorResponse;

    @post
    @route("/suggestions/{id}/review")
    op reviewSuggestion(@path id: int64, @body body: ReviewRequest):
      | ReviewResult
      | BadRequestResponse
      | UnauthorizedResponse
      | NotFoundResponse
      | ConflictResponse
      | InternalServerErrorResponse;

    @get
    @route("/links")
    op listLinks(
      @query document_id?: uuid,
      @query link_type?: LinkType,
      @query origin?: LinkOrigin,
      @query limit?: int32 = 50,
      @query offset?: int32 = 0,
    ): Page<Link> | UnauthorizedResponse | InternalServerErrorResponse;

    @post
    @route("/corpus/import")
    op importCorpus(
      @multipartBody body: {
        archive: HttpPart<File>;
        seed: HttpPart<int64>;
        tune_share: HttpPart<float64>;
      },
    ):
      | CorpusImportResult
      | BadRequestResponse
      | UnauthorizedResponse
      | InternalServerErrorResponse;

    @post
    @route("/experiments")
    op createExperiment(@body body: ExperimentCreate):
      | ExperimentAccepted
      | BadRequestResponse
      | UnauthorizedResponse
      | InternalServerErrorResponse;

    @get
    @route("/experiments/{id}")
    op getExperiment(@path id: int64):
      | ExperimentDetail
      | UnauthorizedResponse
      | NotFoundResponse
      | InternalServerErrorResponse;

    @get
    @route("/experiments/{id}/export")
    op exportExperiment(@path id: int64, @query format: ExportFormat):
      | ExportFile
      | BadRequestResponse
      | UnauthorizedResponse
      | NotFoundResponse
      | InternalServerErrorResponse;
  }
}
