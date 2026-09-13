"""Local llama-server transport and lossless, token-budgeted source chunks."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request
from urllib.request import urlopen

DIMENSION = 384
CONTEXT_SIZE = 512
MODEL_NAME = "granite-97m-r2-q8_0.gguf"
MODEL_SHA256 = "d0aefc589e25df26b75d45eaa3b7205cad850ceeaaef82c6b98b04b166b992d4"
MODEL_ID = f"sha256:{MODEL_SHA256}:mean:512:384:v1"


class EmbeddingError(ValueError):
    """Safe to report without exposing service responses or source text."""


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    char_start: int
    char_end: int
    token_start: int
    token_end: int


def validate_vector(vector):
    """Validate before writing pgvector or performing cosine search."""
    if not isinstance(vector, (list, tuple)) or len(vector) != DIMENSION:
        msg = "Expected a 384-dimensional embedding"
        raise EmbeddingError(msg)
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in vector):
        msg = "Embedding must contain finite numbers"
        raise EmbeddingError(msg)
    norm = math.hypot(*vector)
    if not norm or not math.isfinite(norm):
        msg = "Embedding must have a finite, nonzero norm"
        raise EmbeddingError(msg)
    return [float(v) / norm for v in vector]


class LocalEmbedder:
    """The configured endpoint must serve the pinned GGUF with mean pooling."""

    model_id = MODEL_ID
    dimension = DIMENSION

    def __init__(self, base_url=None, *, timeout=60, context_size=CONTEXT_SIZE):
        self.base_url = (
            base_url
            or os.environ.get(
                "OPENDB_EMBEDDING_URL",
                "http://127.0.0.1:8080",
            )
        ).rstrip("/")
        if urlsplit(self.base_url).scheme not in {"http", "https"}:
            msg = "Embedding endpoint must use HTTP or HTTPS"
            raise EmbeddingError(msg)
        self.timeout = timeout
        self.context_size = context_size

    def _post(self, path, payload):
        request = Request(  # noqa: S310 - constructor enforces HTTP(S).
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.load(response)
        except (URLError, OSError, ValueError) as exc:
            msg = "Local embedding service unavailable or invalid response"
            raise EmbeddingError(msg) from exc

    def tokenize(self, text, *, add_special=False):
        result = self._post(
            "/tokenize",
            {
                "content": text,
                "add_special": add_special,
                "parse_special": True,
            },
        )
        tokens = result.get("tokens") if isinstance(result, dict) else None
        if not isinstance(tokens, list) or any(type(t) is not int for t in tokens):
            msg = "Invalid tokenizer response"
            raise EmbeddingError(msg)
        return tokens

    def detokenize(self, tokens):
        result = self._post("/detokenize", {"tokens": tokens})
        content = result.get("content") if isinstance(result, dict) else None
        if not isinstance(content, str):
            msg = "Invalid detokenizer response"
            raise EmbeddingError(msg)
        return content

    def embed(self, text):
        if not text or len(self.tokenize(text, add_special=True)) > self.context_size:
            msg = "Embedding input is empty or exceeds the token budget"
            raise EmbeddingError(msg)
        result = self._post(
            "/v1/embeddings",
            {
                "model": MODEL_NAME,
                "input": text,
                "encoding_format": "float",
            },
        )
        try:
            data = result["data"]
            vector = data[0]["embedding"]
            index = data[0]["index"]
        except (KeyError, TypeError, IndexError) as exc:
            msg = "Invalid embedding response"
            raise EmbeddingError(msg) from exc
        if len(data) != 1 or index != 0:
            msg = "Invalid embedding response"
            raise EmbeddingError(msg)
        return validate_vector(vector)


def _prefix_length(text, embedder):
    """Use token IDs for a boundary estimate, and re-tokenize the exact source slice.

    Detokenization may normalize text or split a Unicode code point. In that case
    binary search on source character boundaries preserves the original text.
    Every returned slice is checked including the model's special tokens.
    """
    tokens = embedder.tokenize(text)
    overhead = len(embedder.tokenize("", add_special=True))
    budget = embedder.context_size - overhead
    if budget <= 0:
        msg = "Context has no room for source tokens"
        raise EmbeddingError(msg)
    candidate = embedder.detokenize(tokens[:budget])
    if candidate and text.startswith(candidate):
        if len(embedder.tokenize(candidate, add_special=True)) <= embedder.context_size:
            return len(candidate)
    low, high, best = 1, len(text), 0
    while low <= high:
        middle = (low + high) // 2
        if (
            len(embedder.tokenize(text[:middle], add_special=True))
            <= embedder.context_size
        ):
            best, low = middle, middle + 1
        else:
            high = middle - 1
    if not best:
        msg = "A source character exceeds the token budget"
        raise EmbeddingError(msg)
    return best


def chunk_text(text, embedder):
    """Return ordered exact source slices; offsets are zero-based, end-exclusive.

    Token offsets refer to the concatenation of independently tokenized chunks,
    excluding inserted special tokens. Character offsets always address source.
    """
    chunks = []
    char_start = token_start = 0
    while char_start < len(text):
        length = _prefix_length(text[char_start:], embedder)
        piece = text[char_start : char_start + length]
        token_end = token_start + len(embedder.tokenize(piece))
        chunks.append(
            Chunk(
                len(chunks),
                piece,
                char_start,
                char_start + length,
                token_start,
                token_end,
            )
        )
        char_start += length
        token_start = token_end
    return chunks
