# Petrix AI — Layered intent matching

Version `0.4.0` adds semantic embeddings and Groq intent recognition after fuzzy matching. Existing workflow UUIDs, step numbers, saved actions, and accounts are preserved. The intent-matching release itself added no database migration. The current bundle also includes Ask survey, SQL Query, and migrations through `0006`; run `python -m alembic upgrade head` from backend.

## Upgrade on Windows

Stop the backend and Angular. Copy the updated source contents into `C:\Users\Admin\petrix-ai`, preserving your `backend/.env`, virtual environment, PostgreSQL database, and Mailpit installation.

With the existing Petrix virtual environment active, run:

```powershell
cd C:\Users\Admin\petrix-ai
python -m pip install -r .\backend\requirements.lock.txt
```

Add the following settings to your existing `backend/.env`. Put your actual Groq API key in this file on your computer. The key belongs only on the backend.

```dotenv
GROQ_API_KEY=YOUR_GROQ_API_KEY
GROQ_INTENT_MODEL=openai/gpt-oss-20b
INTENT_LLM_ENABLED=true
INTENT_LLM_THRESHOLD=0.80
INTENT_LLM_TIMEOUT_SECONDS=15
INTENT_LLM_MAX_TOKENS=2048

INTENT_SEMANTIC_ENABLED=true
INTENT_SEMANTIC_THRESHOLD=0.70
INTENT_SEMANTIC_MARGIN=0.08
INTENT_FUZZY_MARGIN=0.10
EMBEDDING_PROVIDER=fastembed
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_CACHE_DIR=.cache/embeddings
```

Prepare the local model once, then start the backend:

```powershell
cd C:\Users\Admin\petrix-ai\backend
python -m alembic upgrade head
python -m app.workflows.matching.prepare_embeddings
python -m uvicorn app.main:create_app --factory --reload --port 8000 --no-access-log
```

The first model download requires internet access and can take several minutes. Model files are cached on disk and excluded from source control and release archives. Chat requests load the cached model offline; they do not initiate downloads. After preparing or changing the model, restart the backend.

In the frontend terminal:

```powershell
cd C:\Users\Admin\petrix-ai\frontend
npm ci
npm start
```

Python remains `3.9.12`. The lock file pins Groq SDK `1.0.0`, FastEmbed `0.6.1`, NumPy `1.26.4`, and ONNX Runtime `1.19.2`, together with their dependencies. Use this lock file instead of installing the latest versions of these packages independently.

## Matching order

Choose **Layered intent matching** in a Message received trigger. This retains the existing API value `match_mode: "approximate"`, so previous approximate triggers automatically use the new pipeline when providers are ready.

| Stage | Behaviour |
| --- | --- |
| Fuzzy text | Compares normalized words and spelling against the configured examples. A candidate must meet its trigger's fuzzy threshold and lead the runner-up by `INTENT_FUZZY_MARGIN`. |
| Semantic embeddings | If fuzzy matching is inconclusive, embeds the incoming message and intent examples, normalizes the vectors, and compares cosine similarity. The best candidate must meet the semantic threshold and separation margin. |
| Groq intent recognition | If the preceding stages remain inconclusive, sends the current candidates and bounded recent conversation context to `openai/gpt-oss-20b`. Validates a structured intent result before selecting any workflow. |
| Clarification or fallback | Clarification requests and low-confidence selected intents produce a question without running workflow actions. A no-match or unavailable provider proceeds to the configured fallback at the current level, then follows the existing top-level navigation rules if no local fallback exists. |

A successful stage stops the pipeline; Groq is not called for an accepted fuzzy or semantic match. Closely competing candidates continue to the next stage. Similarity scores measure the selected metric and are not calibrated probabilities. Groq's confidence is a model estimate, with a separate acceptance threshold.

**Exact phrase matching** remains an explicit opt-out from semantic and LLM interpretation. It requires an entire normalized message to equal an example. Exact triggers with a literal match take priority. Existing deterministic ordering resolves duplicate exact matches.

Only enabled Message received triggers at the current sibling level participate. Disabled steps, exact-only candidates, actions, unrelated branches, permanent system events, and attachment triggers cannot be selected by semantic or Groq intent recognition. Language examples use the active workflow language, falling back to the agent's default language when needed.

The agent's **Large language model** switch controls the Groq intent stage and optional generative replies. Disabling it keeps exact, fuzzy, and semantic matching available. The global `INTENT_LLM_ENABLED` setting can disable Groq recognition separately.

## Configure useful intents

Use an informative name and description, then provide several representative examples. The trigger description helps Groq distinguish nearby meanings.

| Intent | Description | Example phrases |
| --- | --- | --- |
| `check_balance` | Requests the current available account balance. | Check my account balance; Show my current funds |
| `perform_transfer` | Wants to begin a transfer, rather than learn the steps. | I want to transfer money; Send funds to another account |
| `transfer_guidance` | Asks for instructions or information about making transfers. | How do I transfer money?; Explain how to send funds |

Attach your saved actions to each trigger. Intent recognition selects a configured workflow; it does not implement banking transactions or retrieve balances by itself.

The default English retrieval model was checked locally using real embeddings. With examples for balance and opening hours, “How much money do I have?” selected `check_balance` at approximately `0.7301` cosine similarity. An unrelated geography question was rejected. This is a smoke check, not a general accuracy benchmark. Test your own examples and thresholds, especially when intent meanings overlap.

The BGE default is an English model. For multilingual semantic matching, you can select a model supported by the pinned FastEmbed release, such as `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, prepare it again, and recalibrate the semantic threshold using your target languages. Cosine scores differ across models; do not copy a threshold unchanged and assume equivalent behaviour.

## Structured results and session data

Groq returns an allowed intent ID or null, confidence, extracted entities, an optional context summary, and a clarification decision. The backend validates the result and checks its ID against the current candidate allow-list. Malformed, truncated, foreign-node, duplicate-entity, and out-of-range results cannot execute actions.

The tester response adds `matching`:

```json
{
  "method": "llm",
  "scope": "root",
  "node_id": "configured-trigger-uuid",
  "node_number": 12,
  "intent_name": "perform_transfer",
  "score": 0.95,
  "entities": { "amount": "500" },
  "needs_clarification": false,
  "clarification_question": null,
  "context_summary": null,
  "attempts": [],
  "warnings": []
}
```

The real `attempts` array records the stages considered, their scope, status, and best score. Open **Intent matching** in the tester to inspect the decision and provider warnings.

Entities are strings and belong to the recognition result. They do not overwrite your custom chat variables. Subsequent actions can explicitly read the result through the existing read-only `system` namespace:

```python
system["intent"]["entities"].get("amount")
```

For a Set variable action, use that expression to assign a field such as `requested_amount`. Convert its type explicitly when needed. Send message supports paths such as `{{system.intent.entities.amount}}` when the value exists. Like other message placeholders, a missing path produces a controlled action error.

The current result is stored with the conversation. A new event replaces it; use Set variable when a recognized value must survive later turns. A clarification preserves the relevant waiting branch, and recent messages include the clarification question on the next recognition request. A changed workflow revision retains the existing rule of clearing outdated nested execution state.

## Provider configuration and failures

Local embeddings use CPU inference and require no embedding API key. To use an OpenAI-compatible embedding service instead, configure its own endpoint and credentials:

```dotenv
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=https://YOUR_EMBEDDING_HOST/v1
EMBEDDING_API_KEY=YOUR_EMBEDDING_API_KEY
EMBEDDING_MODEL=YOUR_EMBEDDING_MODEL
EMBEDDING_TIMEOUT_SECONDS=10
```

Set a threshold appropriate for that model. The embedding endpoint is configured independently of Groq chat recognition. Local model preparation is unnecessary for a remote provider. Production remote connections require HTTPS.

Example embeddings are cached in bounded process memory by workspace, agent, workflow revision, language, provider, model, and example content. Edited examples cannot reuse stale vectors. User-message vectors are reused only inside the current event. No vector database or new PostgreSQL extension is required for this increment.

Missing local files, an unconfigured Groq key, provider timeouts, and provider errors leave fuzzy/exact matching usable. The tester displays controlled warnings for provider failures, and unmatched requests can reach the configured fallback. Raw provider bodies, API keys, and internal exception messages are not returned to the browser. The key and model configuration are read from `backend/.env`; changing them requires a backend restart.

Groq classification and optional generative replies share `LLM_TESTER_REQUESTS_PER_MINUTE`. Each external model call consumes a request; nested and top-level classification may each require one. SDK retries are disabled. Recent context is limited to six messages of up to 1,000 characters each. Custom chat variables and the saved agent persona are not automatically sent to the intent classifier.

The optional unmatched-message generative adapter still uses `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`. Supplying `GROQ_API_KEY` configures intent recognition; generative replies retain their separate connection settings. The reserved **LLM processing action** remains unavailable in this increment.

## Code locations

| File | Responsibility |
| --- | --- |
| `backend/app/workflows/matching/fuzzy.py` | Normalization and the existing text-similarity score. |
| `backend/app/workflows/matching/embeddings.py` | Local/hosted embedding adapters, cosine comparison, and scoped example cache. |
| `backend/app/workflows/matching/groq_intents.py` | Groq SDK request, strict JSON schema, bounded context, and response validation. |
| `backend/app/workflows/matching/router.py` | Stage order, thresholds, ambiguity margins, candidate scope, and fallback selection. |
| `backend/app/workflows/matching/prepare_embeddings.py` | One-time model download and preparation. |
| `backend/app/workflows/runtime.py` | Executes the selected branch, handles clarification, and retains conversation continuations. |
| `backend/app/core/config.py` | `.env` configuration and provider limits. |

Groq's supported strict-output models and schema rules are documented in its [Structured Outputs guide](https://console.groq.com/docs/structured-outputs). FastEmbed model choices are documented in the [supported-model list](https://qdrant.github.io/fastembed/examples/Supported_Models/); the pinned package's `TextEmbedding.list_supported_models()` is authoritative for this installation.
