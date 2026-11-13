# LLM Processing

Version 0.15.0 makes **Add action → AI & data → LLM Processing** Ready. Configure it to summarise, classify, extract information, or generate text, then use its response in later actions. It does not send a message by itself.

## Setup

Keep your existing database and `backend/.env`. From **backend**, with the project virtual environment active:

```powershell
python -m alembic upgrade head
```

This applies migration **0008**, adding agent-owned Knowledge text sources without rewriting existing workflows or chat history. No new runtime dependency is required. Add or confirm these settings in `backend/.env`, then restart the backend:

```dotenv
GROQ_API_KEY=your_groq_key
GROQ_INTENT_MODEL=openai/gpt-oss-20b
LLM_PROCESSING_MAX_TOKENS=4096
```

The model selector displays the server's `GROQ_INTENT_MODEL`. Workflows store the `intent` model alias, so changing that environment setting applies to existing actions after restart. API keys remain on the server. The agent's **Enable LLM** setting must be on. `INTENT_LLM_ENABLED` controls intent recognition separately; it does not disable an explicitly configured LLM Processing action.

## Fields

| Field | How to fill it |
| --- | --- |
| Input | Text or substitutions such as `Summarise this complaint: {description.text}`. Text input supports up to 16,000 characters. |
| Script, below Input | Switch to a safe Python expression, such as `description.text` or `{"description": description.text, "rating": rating.norm.value}`. Expressions support up to 2,000 characters; structured results become JSON text. |
| Model | The configured Groq intent model, default `openai/gpt-oss-20b`. |
| Temperature | A number from 0 to 2; default 1. Try 0.2 for a factual summary. |
| System prompt | Instructions for the task, format, and tone, separately from customer input. Supports variables, up to 8,000 characters, or Script mode with a safe Python expression. |
| Use Knowledge info | Includes all enabled text sources saved in the selected agent's Knowledge tab. Add at least one source first. |
| Resulting variable | A custom name such as `complaint_details`: 1–64 characters, starting with a letter, then letters, numbers, or underscores. Python keywords, built-in function names, read-only system names, and `language` are reserved. |
| Advanced: Timeout after | 1–120 seconds, default 30. A provider timeout follows the Timeout path. |

Script mode uses the existing chat-script interpreter. It supports expressions and chat variables, without imports, arbitrary Python execution, filesystem access, or network access. Switching modes preserves the entered text; adjust it to match the selected mode before saving.

Click **Save**, then add actions beneath **Success**, **Error**, and **Timeout**. These paths work with Branch, Jump, nested surveys, and shared steps after the action. Existing step numbers and outcome path IDs remain stable when editing a saved action.

## Test flow: summarise a complaint

1. Open **Workflows → Chat started** and add **Ask survey**. Ask `Please describe your complaint.` with Text answers and chat variable `description`.
2. Add **LLM Processing** immediately after the survey:

   | Setting | Value |
   | --- | --- |
   | Input | `{description.text}` |
   | Model | `openai/gpt-oss-20b` |
   | Temperature | `0.2` |
   | System prompt | `Extract the main complaint from the customer's description. Return a concise, factual summary. Do not add information the customer did not provide.` |
   | Use Knowledge info | Off |
   | Resulting variable | `complaint_details` |

3. Under **Success**, add **Send message**: `I recorded this summary: {complaint_details}`.
4. Under **Error**, add **Send message**: `I could not summarise that right now. Please try again.`
5. Under **Timeout**, add **Send message**: `That took too long. Please try again shortly.`
6. Restart the tester and answer the question, for example: `I deposited money yesterday but it still does not show in my account.`

The generated summary is stored directly as a **string** in `complaint_details`. Reference `{complaint_details}` in Send message, or use `complaint_details` in a Python expression. Do not add `.body` or `.ok`: this action does not use the Send API response wrapper. Only the message action displays the summary. A later Create Ticket action can use it when that feature is implemented; Create Ticket remains Later in this release.

## Test flow: classify feedback after closure

Under **Chat Closed**, add Collect Feedback with an optional open-response question stored as `feedback_comment`. Add LLM Processing after it:

- Input: `{feedback_comment}`.
- System prompt: `Classify the feedback as positive, neutral, or negative. Return exactly one lowercase word. If no comment was given, return neutral.`
- Temperature: `0`.
- Resulting variable: `feedback_sentiment`.

Under Success, a Branch can check `feedback_sentiment == "negative"` and use Set Variable or Send API to record the outcome. Under Error and Timeout, use Set Variable to record `"unavailable"` in a separate status variable. Ordinary Send message is unavailable after chat closure. Feedback submission resumes the model action while the conversation stays closed.

## Knowledge and language

See [KNOWLEDGE.md](KNOWLEDGE.md) to add text sources. Only this agent's enabled sources are included. Sources and customer input are separate task-data messages; the system prompt supplies the task instructions. The active conversation language is included in the system context. Switch Language before processing when needed, and state any required output format/language in your task prompt.

Knowledge is supplied to Groq with the prompt when enabled. This release supports bounded text sources; document ingestion, website crawling, embeddings, and retrieval ranking are future features. Knowledge text and workflows have separate revision histories from agent settings snapshots and settings-only exports.

## Outcomes and limits

Success requires a complete, non-empty text response. Generated text is capped at 8,192 characters and 16,000 UTF-8 bytes; oversized or incomplete responses follow Error instead of silently truncating. The completion token limit is configured by `LLM_PROCESSING_MAX_TOKENS` (256–8,192). Serialized request context is capped at 128,000 bytes. Existing chat-variable limits also apply: 100 variables and 64 KiB total.

A failed attempt clears an existing result to `null` so downstream steps cannot reuse stale generated text. If no variable slot is available, Error runs without a provider request. A disabled action does not call the provider or change variables. Input values are resolved before replacing the result variable, allowing transformations of an existing value with the same variable name.

At most three processing requests run per incoming event, shared across its normal workflow, closure automation, and feedback continuation/callback. Requests also use the existing per-user model quota (`LLM_TESTER_REQUESTS_PER_MINUTE`, default 20). There are no automatic provider retries. Local or provider rate limits follow Error. This is a synchronous workflow action; provider processing is not undone if a later step causes a database rollback, so a manual retry may make another model request.

| Trace error | Meaning |
| --- | --- |
| `invalid_input` | A variable/expression could not be resolved, or the resolved input was empty/too long. |
| `provider_unavailable` | The server's Groq connection is unavailable or missing its key. |
| `llm_disabled` | LLM is disabled in the agent's settings. |
| `knowledge_unavailable` | Knowledge was requested but no enabled source is available for this agent. |
| `context_too_large` | The serialized request exceeds the context byte limit. |
| `processing_limit` | This event has already used three processing requests. |
| `request_limit` | The per-user model request quota is exhausted. |
| `provider_rate_limit` | Groq returned HTTP 429. |
| `provider_error` | The provider request failed. |
| `invalid_response` | The provider returned an empty, malformed, or incomplete completion. |
| `response_too_large` | The returned text exceeds the response limits. |
| `variable_limit` | The result could not fit the chat-variable limits. |
| `timeout` | The provider exceeded the selected timeout; Timeout runs. |

The tester trace includes outcome, configured model, knowledge source count, and a short error code. It does not expose raw provider errors or API keys. Model accuracy and exact output depend on the selected provider/model; use clear instructions and validate generated text before decisions requiring exact values.

Provider references: [Groq chat completions API](https://console.groq.com/docs/api-reference) and [Groq reasoning output](https://console.groq.com/docs/reasoning). The configured GPT-OSS model requests hidden reasoning and stores only its final answer.
