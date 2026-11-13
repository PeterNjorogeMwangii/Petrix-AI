# Agent Knowledge text sources

Version 0.15.0 enables the selected agent's **Knowledge** tab with a text-source editor. LLM Processing includes these sources when **Use Knowledge info** is enabled.

1. Apply migration **0008** with `python -m alembic upgrade head` from backend.
2. Select the intended agent and open **Knowledge**.
3. Click **Add text source**, give it a title, and enter the information to use.
4. Keep **Enabled** selected, then click **Save knowledge**.
5. Add or edit an LLM Processing action and turn on **Use Knowledge info**.

For example, add a source named `Complaint handling` with text `A complaint reference is required for status checks. Refund processing normally takes five business days.` Ask the model to answer the customer's question using that reference text, and to say when the supplied information is insufficient.

The editor supports up to **20 sources**, a 100-character title and 20,000-character body per source, and **64 KiB** of combined UTF-8 title/body text. Disabled sources stay saved but are excluded from processing. Removing a source takes effect after saving. Plain text is displayed and edited as text; this editor does not crawl URLs, ingest documents, or build a vector index.

Knowledge belongs to one agent. Reading requires a signed-in workspace member; changes require **Manage AI Agents** and CSRF protection. Save uses `expected_revision`, rejects stale edits with HTTP 409, and retains the unsaved draft in the editor on failure. Reload asks before discarding a draft. Navigating away, switching agents, and closing the page use unsaved-change protection. Each changed save creates a `knowledge_saved` audit entry without copying source text into the log; unchanged saves do not increment the revision.

Knowledge is versioned separately from workflows and agent settings. Existing settings-only agent exports/imports and version restore do not include or restore sources. Deleting an agent removes its knowledge. Save source text separately if you need to transfer it between agents.

When an action requests knowledge, all enabled sources for its current agent are included in the model request; another agent's sources are not included. If no enabled source exists, the action follows Error with `knowledge_unavailable`. The prompt and selected sources are sent to the configured Groq model for processing. See [LLM_PROCESSING.md](LLM_PROCESSING.md) for outcome behavior and context limits.

## API

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/workspaces/{workspace_id}/agents/{agent_id}/knowledge` | Returns `agent_id`, `revision`, `sources`, and `updated_at`. An unsaved document has revision 1, empty sources, and null timestamp. GET does not create a row. |
| POST | Same path | Accepts `expected_revision` and `sources`; returns the saved document. Each source has UUID `id`, `name`, `content`, and boolean `enabled`. |

A changed first save returns revision 2. Source IDs remain stable across title/content edits. The schema rejects unknown fields, duplicate IDs, blank content, and oversized documents. Sources are saved in the application's PostgreSQL `agent_knowledge` table, separate from Database Connections.
