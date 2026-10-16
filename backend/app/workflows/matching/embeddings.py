"""Real neural embeddings with a bounded, revision-scoped example cache."""
import hashlib
import json
import math
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from time import monotonic

import httpx

from app.workflows.matching.types import ProviderUnavailable


def unit_vector(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 4096:
        raise ProviderUnavailable()
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
        raise ProviderUnavailable()
    norm = math.hypot(*values)
    if not norm or not math.isfinite(norm):
        raise ProviderUnavailable()
    return tuple(value / norm for value in values)


def cosine(left, right):
    if len(left) != len(right):
        raise ProviderUnavailable()
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def cache_directory(settings):
    path = Path(settings.embedding_cache_dir).expanduser()
    return path if path.is_absolute() else Path(__file__).resolve().parents[3] / path


class EmbeddingClient:
    def __init__(self, settings):
        self.settings = settings
        self._model = None
        self._lock = RLock()
        self._retry_at = 0.0

    def embed(self, texts):
        try:
            if self.settings.embedding_provider == "openai_compatible":
                if not self.settings.embedding_base_url:
                    raise ProviderUnavailable()
                headers = {}
                key = self.settings.embedding_api_key.get_secret_value()
                if key:
                    headers["Authorization"] = "Bearer " + key
                vectors = []
                deadline = monotonic() + self.settings.embedding_timeout_seconds
                with httpx.Client(timeout=self.settings.embedding_timeout_seconds, follow_redirects=False) as client:
                    for start in range(0, len(texts), 64):
                        remaining = deadline - monotonic()
                        if remaining <= 0:
                            raise ProviderUnavailable()
                        batch = texts[start:start + 64]
                        response = client.post(self.settings.embedding_base_url.rstrip("/") + "/embeddings",
                            headers=headers, timeout=remaining,
                            json={"model": self.settings.embedding_model, "input": batch, "encoding_format": "float"})
                        response.raise_for_status()
                        rows = response.json()["data"]
                        if not isinstance(rows, list) or len(rows) != len(batch):
                            raise ProviderUnavailable()
                        by_index = {}
                        for row in rows:
                            index = row["index"]
                            if type(index) is not int or index in by_index or not 0 <= index < len(batch):
                                raise ProviderUnavailable()
                            by_index[index] = unit_vector(row["embedding"])
                        vectors.extend(by_index[index] for index in range(len(batch)))
                return vectors
            with self._lock:
                if monotonic() < self._retry_at:
                    raise ProviderUnavailable()
                if self._model is None:
                    from fastembed import TextEmbedding
                    # Model downloads belong to prepare_embeddings, never a chat request.
                    self._model = TextEmbedding(model_name=self.settings.embedding_model,
                        cache_dir=str(cache_directory(self.settings)), threads=2,
                        providers=["CPUExecutionProvider"], local_files_only=True)
                return [unit_vector(value.tolist()) for value in self._model.embed(texts, batch_size=64)]
        except ProviderUnavailable:
            raise
        except Exception:
            # ONNX, model download, transport, and provider errors may contain secrets.
            self._retry_at = monotonic() + 30
            raise ProviderUnavailable() from None


class SemanticMatcher:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings
        self._cache = OrderedDict()
        self._units = 0
        self._lock = RLock()

    def rank(self, candidates, message, scope_key, query_cache):
        texts = list(dict.fromkeys(example for candidate in candidates for example in candidate["examples"]))
        digest = hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode("utf-8")).hexdigest()
        key = tuple(scope_key) + (self.settings.embedding_provider, self.settings.embedding_model, digest)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                vectors = self.client.embed(texts)
                if len(vectors) != len(texts):
                    raise ProviderUnavailable()
                entry = dict(zip(texts, vectors))
                units = sum(len(value) for value in vectors)
                if units <= 2_000_000:
                    while self._cache and (len(self._cache) >= 32 or self._units + units > 2_000_000):
                        _, removed = self._cache.popitem(last=False)
                        self._units -= sum(len(value) for value in removed.values())
                    self._cache[key] = entry
                    self._units += units
            else:
                self._cache.move_to_end(key)
        query_key = (self.settings.embedding_provider, self.settings.embedding_model, message)
        if query_key not in query_cache:
            query = self.client.embed([message])
            if len(query) != 1:
                raise ProviderUnavailable()
            query_cache[query_key] = query[0]
        query = query_cache[query_key]
        return sorted([(max(cosine(query, entry[text]) for text in candidate["examples"]), candidate)
            for candidate in candidates], key=lambda item: item[0], reverse=True)
