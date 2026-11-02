"""Local dense BGE-M3, explicit token windows and content-addressed input identity.

No ORM, labels or LLM extractions are accepted here. Model loading is lazy, CPU-only,
and offline by default; queue, vector persistence and stage timings are separate adapters.
"""

import hashlib
import json
import logging
import math
import platform
import re
import time
import unicodedata
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)
MODEL_ID = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DIMENSIONS = 1024


class EmbeddingError(Exception):
    """Processing failure, never a signal to switch to another embedder."""


class EncodingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: Literal["BAAI/bge-m3"] = MODEL_ID
    revision: Literal["5617a9f61b028005a4858fdac845db406aefb181"] = MODEL_REVISION
    preparation: Literal["semantic-text-v1"] = "semantic-text-v1"
    encoding: Literal["dense-cls-token-weighted-v1"] = "dense-cls-token-weighted-v1"
    dimensions: Literal[1024] = DIMENSIONS
    chunk_tokens: int = Field(default=512, ge=32, le=8192)
    max_chunks: int = Field(default=256, ge=1)
    max_input_chars: int = Field(default=1_000_000, ge=1)
    device: Literal["cpu"] = "cpu"
    dtype: Literal["float32"] = "float32"
    attention: Literal["sdpa"] = "sdpa"
    normalize: Literal[True] = True
    batch_size: Literal[1] = 1
    cpu_threads: int = Field(default=2, ge=1, le=32)


@dataclass(frozen=True)
class PreparedText:
    text: str
    input_hash: str
    text_hash: str
    snapshot: str
    original_chars: int


@dataclass(frozen=True)
class EmbeddingResult:
    input_hash: str
    text_hash: str
    snapshot: str
    vector: tuple[float, ...]
    original_chars: int
    prepared_chars: int
    tokens: int
    chunks: int
    truncated: bool
    duration_ms: int


def runtime_versions() -> dict[str, str]:
    return {
        name: version(name) for name in ("sentence-transformers", "transformers", "torch", "numpy")
    }


def clean_text(text: str) -> str:
    """NFKC, remove non-whitespace control/format characters, collapse whitespace.

    Do not remove numbers, dates, names, legal clauses or add a model-dependent subject.
    """
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(
        char
        for char in normalized
        if char.isspace() or not unicodedata.category(char).startswith("C")
    )
    return re.sub(r"\s+", " ", normalized).strip()


def prepare_text(text: str, config: EncodingConfig) -> PreparedText:
    if len(text) > config.max_input_chars:
        raise EmbeddingError("Превышен лимит символов смыслового входа; текст не обрезан")
    cleaned = clean_text(text)
    if not cleaned:
        raise EmbeddingError("Пустой смысловой вход после очистки")
    snapshot = json.dumps(
        {
            "config": config.model_dump(),
            "packages": runtime_versions(),
            "machine": platform.machine(),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    text_hash = hashlib.sha256(cleaned.encode()).hexdigest()
    input_hash = hashlib.sha256((snapshot + "\n" + cleaned).encode()).hexdigest()
    return PreparedText(cleaned, input_hash, text_hash, snapshot, len(text))


def token_windows(
    tokens: list[int], special_tokens: int, config: EncodingConfig
) -> tuple[list[int], ...]:
    payload = config.chunk_tokens - special_tokens
    if payload <= 0:
        raise EmbeddingError("Некорректное число специальных токенов")
    if not tokens:
        raise EmbeddingError("Токенизатор вернул пустой вход")
    count = math.ceil(len(tokens) / payload)
    if count > config.max_chunks:
        raise EmbeddingError("Превышен лимит окон смыслового входа; токены не обрезаны")
    return tuple(tokens[start : start + payload] for start in range(0, len(tokens), payload))


def normalized_vector(values: Any) -> tuple[float, ...]:
    vector = np.asarray(values, dtype=np.float32)
    if vector.shape != (DIMENSIONS,) or not np.isfinite(vector).all():
        raise EmbeddingError("Невалидная размерность или нечисловой вектор BGE-M3")
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 0:
        raise EmbeddingError("Нулевой или невалидный вектор BGE-M3")
    return tuple(float(value) for value in vector / norm)


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    # Validate also vectors retrieved from persistence, not just fresh model outputs.
    a, b = normalized_vector(left), normalized_vector(right)
    return max(-1.0, min(1.0, float(np.dot(a, b))))


class BGEEmbedder:
    def __init__(
        self,
        cache_dir: str | Path,
        *,
        config: EncodingConfig | None = None,
        local_files_only: bool = True,
        cpu_threads: int | None = None,
    ) -> None:
        if cpu_threads is not None and not 1 <= cpu_threads <= 32:
            raise ValueError("Число CPU-потоков должно быть от 1 до 32")
        self.config = config or EncodingConfig(cpu_threads=cpu_threads or 2)
        if cpu_threads is not None and cpu_threads != self.config.cpu_threads:
            raise ValueError("Число CPU-потоков не соответствует снимку конфигурации")
        self.cache_dir = str(cache_dir)
        self.local_files_only = local_files_only
        self.cpu_threads = self.config.cpu_threads
        self._model: SentenceTransformer | None = None

    def _load(self) -> "SentenceTransformer":
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            torch.set_num_threads(self.cpu_threads)
            try:
                model = SentenceTransformer(
                    self.config.model,
                    revision=self.config.revision,
                    device="cpu",
                    cache_folder=self.cache_dir,
                    local_files_only=self.local_files_only,
                    trust_remote_code=False,
                    model_kwargs={
                        "dtype": torch.float32,
                        "low_cpu_mem_usage": True,
                        "attn_implementation": self.config.attention,
                    },
                )
                model.eval()
                if model.get_sentence_embedding_dimension() != DIMENSIONS:
                    raise EmbeddingError("Размерность модели отличается от 1024")
                self._model = model
            except EmbeddingError:
                raise
            except Exception as exc:
                # Do not store raw library errors: they can include input text or remote URLs.
                raise EmbeddingError(f"Не удалось загрузить BGE-M3: {type(exc).__name__}") from exc
        return self._model

    def encode(self, prepared: PreparedText) -> EmbeddingResult:
        start = time.perf_counter()
        expected = prepare_text(prepared.text, self.config)
        if expected.input_hash != prepared.input_hash or expected.snapshot != prepared.snapshot:
            raise EmbeddingError("Подготовленный вход не соответствует конфигурации BGE-M3")
        try:
            import torch

            model = self._load()
            tokenizer = model.tokenizer
            tokens = tokenizer(
                prepared.text,
                add_special_tokens=False,
                truncation=False,
                return_attention_mask=False,
            )["input_ids"]
            windows = token_windows(
                tokens, tokenizer.num_special_tokens_to_add(pair=False), self.config
            )
            total = np.zeros(DIMENSIONS, dtype=np.float64)
            with torch.inference_mode():
                for window in windows:
                    # Feed token IDs directly: decoding/re-tokenizing windows can silently
                    # expand their length. No tokenizer or ST.encode truncation is involved.
                    encoded = tokenizer.prepare_for_model(
                        window,
                        add_special_tokens=True,
                        truncation=False,
                        return_attention_mask=True,
                        return_tensors="pt",
                    )
                    # prepare_for_model handles a single sequence, so its tensors
                    # have no batch axis. The transformer always expects [1, tokens].
                    encoded = {key: value.unsqueeze(0) for key, value in encoded.items()}
                    if encoded["input_ids"].shape[1] > self.config.chunk_tokens:
                        raise EmbeddingError("Окно превышает явный лимит токенов")
                    output = model(dict(encoded))["sentence_embedding"][0].cpu().numpy()
                    vector = normalized_vector(output)
                    total += np.asarray(vector) * len(window)
            result = EmbeddingResult(
                input_hash=prepared.input_hash,
                text_hash=prepared.text_hash,
                snapshot=prepared.snapshot,
                vector=normalized_vector(total),
                original_chars=prepared.original_chars,
                prepared_chars=len(prepared.text),
                tokens=len(tokens),
                chunks=len(windows),
                truncated=False,
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            logger.info(
                "BGE-M3 encoded input=%s tokens=%s chunks=%s duration_ms=%s",
                result.input_hash,
                result.tokens,
                result.chunks,
                result.duration_ms,
            )
            return result
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"Ошибка кодирования BGE-M3: {type(exc).__name__}") from exc


def download_model(cache_dir: str | Path) -> str:
    """Explicit weight download; corpus text is never transmitted to Hugging Face."""
    from huggingface_hub import snapshot_download

    return snapshot_download(
        MODEL_ID,
        revision=MODEL_REVISION,
        cache_dir=str(cache_dir),
        allow_patterns=["*.json", "pytorch_model.bin", "sentencepiece.bpe.model", "1_Pooling/*"],
        ignore_patterns=["onnx/*"],
    )
