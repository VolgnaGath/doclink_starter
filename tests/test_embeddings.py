import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from app.services.embeddings import (
    BGEEmbedder,
    EmbeddingError,
    EncodingConfig,
    clean_text,
    cosine,
    normalized_vector,
    prepare_text,
    token_windows,
)


def test_cleaning_preserves_document_content() -> None:
    text = "\ufeffДоговор\u00a0№ １２\x00\r\nот 01.02.2025.\tООО «Ёлка»\u200b, 125 ₽."
    assert clean_text(text) == "Договор No 12 от 01.02.2025. ООО «Ёлка», 125 ₽."
    assert clean_text(clean_text(text)) == clean_text(text)


def test_cache_identity_uses_prepared_content_and_encoding_parameters() -> None:
    config = EncodingConfig()
    a = prepare_text("Договор\nпоставки", config)
    b = prepare_text(" Договор  поставки ", config)
    assert a.input_hash == b.input_hash
    assert a.original_chars != b.original_chars
    assert a.text_hash == b.text_hash
    assert prepare_text("Договор аренды", config).input_hash != a.input_hash
    changed = prepare_text(a.text, EncodingConfig(chunk_tokens=256))
    assert changed.text_hash == a.text_hash
    assert changed.input_hash != a.input_hash
    assert "5617a9f61b028005a4858fdac845db406aefb181" in a.snapshot
    for label in ("deal_id", "split", "hard_types", "gold", "filename"):
        with pytest.raises(ValidationError):
            EncodingConfig.model_validate({label: "forbidden"})


def test_empty_and_oversized_inputs_are_errors_not_truncation() -> None:
    for text in ("", " \n\t", "\x00\u200b"):
        with pytest.raises(EmbeddingError, match="Пустой"):
            prepare_text(text, EncodingConfig())
    with pytest.raises(EmbeddingError, match="не обрезан"):
        prepare_text("abcdef", EncodingConfig(max_input_chars=5))


@pytest.mark.parametrize("size", [1, 510, 511, 1020, 1021])
def test_token_windows_cover_every_token_without_overlap_or_truncation(size: int) -> None:
    tokens = list(range(size))
    windows = token_windows(tokens, 2, EncodingConfig())
    assert [token for window in windows for token in window] == tokens
    assert all(0 < len(window) <= 510 for window in windows)
    assert len(windows) == math.ceil(size / 510)


def test_excessive_windows_fail_explicitly() -> None:
    with pytest.raises(EmbeddingError, match="не обрезаны"):
        token_windows(list(range(511)), 2, EncodingConfig(max_chunks=1))
    with pytest.raises(EmbeddingError, match="пустой"):
        token_windows([], 2, EncodingConfig())
    with pytest.raises(EmbeddingError, match="специальных"):
        token_windows([1], 512, EncodingConfig())


def test_vectors_are_unit_length_and_cosine_is_not_unchecked_dot_product() -> None:
    vector = normalized_vector(np.ones(1024) * 3)
    assert sum(value * value for value in vector) == pytest.approx(1)
    assert cosine(vector, vector) == pytest.approx(1)
    assert cosine(tuple(-v for v in vector), vector) == pytest.approx(-1)
    a, b = [0.0] * 1024, [0.0] * 1024
    a[0], b[1] = 2, 5
    assert cosine(tuple(a), tuple(b)) == 0
    for values in ([0.0] * 1024, [1.0] * 768, [float("nan")] * 1024, [float("inf")] * 1024):
        with pytest.raises(EmbeddingError):
            normalized_vector(values)


def test_prepared_input_cannot_be_reused_with_a_different_encoder(tmp_path: Path) -> None:
    prepared = prepare_text("Договор", EncodingConfig())
    engine = BGEEmbedder(tmp_path, config=EncodingConfig(chunk_tokens=256))
    with pytest.raises(EmbeddingError, match="не соответствует"):
        engine.encode(prepared)
    assert engine._model is None
    with pytest.raises(EmbeddingError, match="не соответствует"):
        BGEEmbedder(tmp_path).encode(dataclasses.replace(prepared, text="другой текст"))


def test_missing_model_remains_a_processing_error(tmp_path: Path) -> None:
    engine = BGEEmbedder(tmp_path, local_files_only=True)
    with pytest.raises(EmbeddingError, match="загрузить BGE-M3"):
        engine.encode(prepare_text("Договор поставки", engine.config))
    assert engine._model is None
