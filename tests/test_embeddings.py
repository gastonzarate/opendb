"""Embedding transport and lossless token-budget chunking contracts."""

import json
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

import pytest


class CharacterEmbedder:
    model_id = "test-character-384"
    dimension = 384
    context_size = 8

    def tokenize(self, text, *, add_special=False):
        tokens = [ord(char) + 10 for char in text]
        return [1, *tokens, 2] if add_special else tokens

    def detokenize(self, tokens):
        return "".join(chr(token - 10) for token in tokens if token > 2)

    def embed(self, text):
        return [1.0] + [0.0] * 383


def test_chunks_fit_special_token_budget_and_preserve_source_offsets():
    from opendb.vectors.embeddings import chunk_text

    text = "  café 🐈\n水 goes here!"
    chunks = chunk_text(text, CharacterEmbedder())
    assert "".join(chunk.text for chunk in chunks) == text
    assert [(c.ordinal, c.char_start, c.char_end) for c in chunks] == [
        (0, 0, 6),
        (1, 6, 12),
        (2, 12, 18),
        (3, 18, 21),
    ]
    assert [(c.token_start, c.token_end) for c in chunks] == [
        (0, 6),
        (6, 12),
        (12, 18),
        (18, 21),
    ]
    assert all(
        len(CharacterEmbedder().tokenize(c.text, add_special=True)) <= 8 for c in chunks
    )


def test_normalizing_tokenizer_cannot_change_original_provenance():
    from opendb.vectors.embeddings import chunk_text

    class Normalizing(CharacterEmbedder):
        def detokenize(self, tokens):
            return super().detokenize(tokens).strip().lower()

    text = "  ABCD\n EF 水 "
    chunks = chunk_text(text, Normalizing())
    assert "".join(c.text for c in chunks) == text
    assert all(c.text == text[c.char_start : c.char_end] for c in chunks)
    assert all(
        len(Normalizing().tokenize(c.text, add_special=True)) <= 8 for c in chunks
    )


def test_empty_text_has_no_chunks_and_impossible_budget_fails():
    from opendb.vectors.embeddings import EmbeddingError
    from opendb.vectors.embeddings import chunk_text

    assert chunk_text("", CharacterEmbedder()) == []
    embedder = CharacterEmbedder()
    embedder.context_size = 2
    with pytest.raises(EmbeddingError):
        chunk_text("word", embedder)


@pytest.mark.parametrize(
    "vector",
    [
        [1.0] * 383,
        [0.0] * 384,
        [float("nan")] * 384,
        [float("inf")] * 384,
        [True] * 384,
    ],
)
def test_invalid_vectors_are_rejected(vector):
    from opendb.vectors.embeddings import EmbeddingError
    from opendb.vectors.embeddings import validate_vector

    with pytest.raises(EmbeddingError):
        validate_vector(vector)


@pytest.fixture
def embedding_server():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            fake = CharacterEmbedder()
            if self.path == "/tokenize":
                result = {
                    "tokens": fake.tokenize(
                        body["content"], add_special=body["add_special"]
                    )
                }
            elif self.path == "/detokenize":
                result = {"content": fake.detokenize(body["tokens"])}
            elif self.path == "/v1/embeddings":
                # Wrong payload shape/budget/model cannot produce a valid embedding.
                if (
                    not isinstance(body["input"], str)
                    or len(fake.tokenize(body["input"], add_special=True)) > 8
                    or body["model"] != "granite-97m-r2-q8_0.gguf"
                ):
                    self.send_error(400)
                    return
                result = {
                    "data": [{"index": 0, "embedding": fake.embed(body["input"])}]
                }
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def test_local_http_transport_tokenizes_detokenizes_and_embeds(embedding_server):
    from opendb.vectors.embeddings import LocalEmbedder
    from opendb.vectors.embeddings import chunk_text

    embedder = LocalEmbedder(embedding_server, context_size=8)
    chunks = chunk_text("cats and dogs", embedder)
    assert [c.text for c in chunks] == ["cats a", "nd dog", "s"]
    assert embedder.embed(chunks[0].text) == [1.0] + [0.0] * 383


def test_http_failure_is_sanitized_and_long_queries_fail(embedding_server):
    from opendb.vectors.embeddings import EmbeddingError
    from opendb.vectors.embeddings import LocalEmbedder

    embedder = LocalEmbedder(embedding_server, context_size=8)
    with pytest.raises(EmbeddingError):
        embedder.embed("this input exceeds the budget")
    embedder = LocalEmbedder(embedding_server + "/missing")
    with pytest.raises(EmbeddingError):
        embedder.embed("secret")


def test_worker_rejects_unpinned_model_before_connecting(tmp_path):
    from opendb.vectors.embeddings import EmbeddingError
    from opendb.vectors.worker import verify_model

    artifact = tmp_path / "wrong.gguf"
    artifact.write_bytes(b"wrong weights")
    with pytest.raises(EmbeddingError, match="checksum"):
        verify_model(artifact)
