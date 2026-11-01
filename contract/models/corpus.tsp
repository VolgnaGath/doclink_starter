namespace DocLink;

model CorpusImportResult {
  corpus_version_id: int64;
  documents_imported: int32;
  links_imported: int32;
  tune_documents: int32;
  test_documents: int32;
}
