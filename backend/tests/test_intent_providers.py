import json
import math
import sys
from types import SimpleNamespace

import httpx
import pytest
from groq import Groq

from app.core.config import Settings
from app.workflows.matching import embeddings, groq_intents
from app.workflows.matching.embeddings import EmbeddingClient, SemanticMatcher, cosine, unit_vector
from app.workflows.matching.types import ProviderUnavailable


@pytest.fixture
def matching_settings():
    return Settings(_env_file=None, app_env="test", database_url="postgresql+psycopg://test@localhost/test",
        session_signing_secret="test-only-secret-with-at-least-32-characters", groq_api_key="test-provider-key")


def candidates():
    return [{"node": {"id": "allowed-id", "name": "check_balance", "description": "Show the current balance."},
        "examples": ["Check my balance"]}]


def result(**changes):
    return {"intent_id": "allowed-id", "confidence": 0.95, "entities": [{"name": "currency", "value": "KES"}],
        "needs_clarification": False, "clarification_question": None, "context_summary": None, **changes}


def test_groq_adapter_sends_env_key_strict_schema_candidates_and_bounded_history(matching_settings, monkeypatch):
    captured = []
    def respond(request):
        captured.append(request)
        return httpx.Response(200, json={"id": "test", "created": 0, "object": "chat.completion", "model": "openai/gpt-oss-20b",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(result())}}]})
    def sdk(**kwargs):
        return Groq(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False))
    monkeypatch.setattr(groq_intents, "Groq", sdk)
    recognizer = groq_intents.GroqIntentRecognizer(matching_settings)
    response = recognizer.recognize(candidates(), "How much money do I have?", "en",
        [{"role": "user", "content": "x" * 2000}] * 10)
    assert response.intent_id == "allowed-id" and response.entities[0].value == "KES"
    assert captured[0].headers["authorization"] == "Bearer test-provider-key"
    payload = json.loads(captured[0].content)
    assert payload["model"] == "openai/gpt-oss-20b" and payload["temperature"] == 0
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["schema"]["properties"]["intent_id"]["enum"] == ["allowed-id", None]
    data = json.loads(payload["messages"][1]["content"])
    assert len(data["recent_history"]) == 6 and len(data["recent_history"][0]["content"]) == 1000
    assert data["message"] == "How much money do I have?"
    assert "test-provider-key" not in payload["messages"][0]["content"] + payload["messages"][1]["content"]


@pytest.mark.parametrize("body,finish", [
    (result(intent_id="unknown-or-disabled-node"), "stop"),
    (result(confidence=float("nan")), "stop"),
    (result(confidence=1.1), "stop"),
    (result(needs_clarification="false"), "stop"),
    (result(entities=[{"name": "bad.name", "value": "value"}]), "stop"),
    (result(entities=[{"name": "a", "value": "1"}, {"name": "a", "value": "2"}]), "stop"),
    (result(extra="unsupported"), "stop"),
    (result(), "length"),
    ("malformed { json", "stop"),
])
def test_groq_adapter_rejects_invalid_or_truncated_output(matching_settings, monkeypatch, body, finish):
    def respond(request):
        return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": finish,
            "message": {"role": "assistant", "content": body if isinstance(body, str) else json.dumps(body)}}]})
    monkeypatch.setattr(groq_intents, "Groq", lambda **kwargs: Groq(**kwargs,
        http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)))
    with pytest.raises(ProviderUnavailable):
        groq_intents.GroqIntentRecognizer(matching_settings).recognize(candidates(), "message", "en", [])


def test_groq_failures_are_not_exposed_or_retried(matching_settings, monkeypatch):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "private-provider-body-and-key"})
    monkeypatch.setattr(groq_intents, "Groq", lambda **kwargs: Groq(**kwargs,
        http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)))
    with pytest.raises(ProviderUnavailable) as error:
        groq_intents.GroqIntentRecognizer(matching_settings).recognize(candidates(), "message", "en", [])
    assert len(calls) == 1 and "private" not in str(error.value)


def test_hosted_embedding_adapter_orders_and_normalizes_vectors(matching_settings, monkeypatch):
    matching_settings.embedding_provider = "openai_compatible"
    matching_settings.embedding_base_url = "https://embeddings.example/v1"
    captured = []
    real_client = httpx.Client
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 4]}, {"index": 0, "embedding": [3, 0]}]})
    monkeypatch.setattr(embeddings.httpx, "Client", lambda **kwargs: real_client(**kwargs,
        transport=httpx.MockTransport(respond), trust_env=False))
    assert EmbeddingClient(matching_settings).embed(["first", "second"]) == [(1.0, 0.0), (0.0, 1.0)]
    assert captured[0]["input"] == ["first", "second"] and captured[0]["encoding_format"] == "float"


@pytest.mark.parametrize("values", [[], [0, 0], [True, 1], ["1", 2], [math.inf, 1], [math.nan, 1]])
def test_invalid_embeddings_cannot_drive_routing(values):
    with pytest.raises(ProviderUnavailable): unit_vector(values)


def test_cosine_uses_vector_direction_and_checks_dimensions():
    assert cosine(unit_vector([3, 0]), unit_vector([1, 0])) == 1
    assert cosine(unit_vector([1, 0]), unit_vector([0, 4])) == 0
    with pytest.raises(ProviderUnavailable): cosine((1.0, 0.0), (1.0,))


def test_semantic_cache_is_scoped_to_agent_revision_language_and_text(matching_settings):
    class Vectors:
        def __init__(self): self.calls = []
        def embed(self, texts):
            self.calls.append(list(texts))
            return [unit_vector([1, 0]) for _ in texts]
    client = Vectors(); matcher = SemanticMatcher(client, matching_settings)
    cache = {}
    key = ("workspace", "agent", 1, "en")
    assert matcher.rank(candidates(), "paraphrase", key, cache)[0][0] == 1
    matcher.rank(candidates(), "paraphrase", key, cache)
    assert client.calls == [["Check my balance"], ["paraphrase"]]
    for changed in [("workspace", "agent", 2, "en"), ("workspace", "other-agent", 1, "en"), ("workspace", "agent", 1, "sw")]:
        matcher.rank(candidates(), "paraphrase", changed, cache)
    assert len(client.calls) == 5  # Query vector reused only inside this turn.
    changed = candidates(); changed[0]["examples"] = ["Edited example"]
    matcher.rank(changed, "paraphrase", key, cache)
    assert client.calls[-1] == ["Edited example"]


def test_chat_requests_load_local_model_offline_and_back_off_on_missing_files(matching_settings, monkeypatch):
    calls = []
    def missing(**kwargs):
        calls.append(kwargs)
        raise ValueError("private local path")
    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=missing))
    client = EmbeddingClient(matching_settings)
    for _ in range(2):
        with pytest.raises(ProviderUnavailable): client.embed(["message"])
    assert len(calls) == 1 and calls[0]["local_files_only"] is True
    assert calls[0]["providers"] == ["CPUExecutionProvider"]


@pytest.mark.parametrize("values", [
    {"intent_semantic_threshold": float("nan")}, {"intent_fuzzy_margin": -0.1},
    {"intent_llm_threshold": 1.1}, {"intent_llm_timeout_seconds": 0},
    {"embedding_base_url": "https://user:password@provider.example/v1"},
])
def test_matching_settings_validate_provider_limits_and_secrets(values):
    with pytest.raises(ValueError):
        Settings(_env_file=None, database_url="postgresql+psycopg://test@localhost/test",
            session_signing_secret="test-only-secret-with-at-least-32-characters", **values)
