# Petrix AI verification

## Jump update — 16 October 2026, version 0.4.1

The Jump update passed the complete backend suite: **308 tests passed, no skips or failures**. This includes 27 Jump cases covering UUID configuration, forward references, action and trigger destinations, sibling/ancestor continuations, nested waits, repeated menu visits after new input, chained jumps, variables, disabled steps, missing/foreign/self targets, stable numbers, deletion checks, API persistence, and rollback of looping turns.

The suite ran on CPython 3.12.14 against a temporary PGlite PostgreSQL engine using psycopg, SQLAlchemy, the actual migrations, and isolated schemas. No user's database or provider credentials were used. All backend modules, tests, project scripts, and the optional Groq smoke script also parse with Python 3.9 syntax. Exact Python 3.9.12 execution for this update was not repeated; the new action uses only existing dependencies and Python 3.9-compatible constructs.

The Angular production build passed TypeScript, strict template checks, and all bundle/style budgets. The initial bundle is approximately 291 kB raw; the dashboard lazy chunk is approximately 187 kB raw.

Chromium browser checks used the built frontend and the real authenticated API backed by the temporary database. They verified catalogue creation of a new Jump, name/number search, selected-target display, clearing a target and disabling Save, changing from an action to a trigger target, save/reopen persistence, mobile target selection and Save, complaint confirmation followed by the existing menu, and destination numbers in the execution trace. Desktop and 390 px mobile screenshots were visually inspected. No browser JavaScript errors were observed.

No migration or new dependency is required. Schema head remains `0004`. The optional root `test_groq.py` now reads `GROQ_API_KEY` from the existing `backend/.env` instead of embedding a credential. That live-provider script was not executed. Keep the existing environment files when applying this source update.

## Previous baseline — version 0.4.0

Updated on 15 October 2026 for version `0.4.0`, layered fuzzy/semantic/Groq intent recognition, permanent workflow step numbers, Set variable, and message placeholders. Backend compatibility remains Python 3.9.12.

Version `0.3.3` assigns a permanent per-agent number to every saved workflow trigger and action. Migration `0004` preserves top-level list numbers and assigns numbers to existing descendants. A locked counter allocates new numbers without reusing deleted ones. The builder shows numbers at every level, and the tester uses the same numbers in execution details. Bold assistant formatting from `0.3.2` remains: Angular renders `**text**` and substituted values such as `**{company}**` with actual `strong` elements and text interpolation.

Version `0.4.0` adds real local/hosted embedding adapters, a scoped example-vector cache, cosine similarity, structured Groq recognition, ambiguity margins, confidence gates, clarification, bounded conversation context, entity results, and matching diagnostics in the tester. All existing database migrations remain unchanged and schema head is still `0004`.

## Previous completed checks

| Check | Result |
| --- | --- |
| Backend authentication, workspace, identity validation, agent-management, workflow, expression, numbering, and matching tests under exact CPython 3.9.12 | 281 passed; no failures or skips |
| Angular production build, TypeScript, strict template checks, and bundle budgets | Passed |
| Alembic migrations `0001` through `0004` | Applied successfully |
| Upgrade of an existing `0001` workspace | Default agent, first version, and three role grants backfilled once |
| Upgrade of an existing `0002` agent | Workflow document and greeting backfilled once; repeated upgrade does not reset edited data |
| Upgrade of an edited `0003` workflow | Root numbers retained; nested steps numbered; UUIDs, content, revision, and timestamp preserved; repeat upgrade and downgrade checked |
| Number allocation and persistence | Defaults, all nested node kinds, reparenting/reordering/renaming, disabling, deletion without reuse, server ownership, older client input, rejected/stale saves, independent agents, and tester traces passed |
| Layer order and branch behaviour | Exact/fuzzy short circuit, semantic match before fallback, ambiguity passed to Groq, current-level candidate restrictions, disabled/exact-only exclusions, nested continuations, and non-message events passed |
| Structured Groq recognition | Real SDK with mocked HTTP transport: environment key, requested model, strict schema, allowed IDs, bounded context, malformed/truncated/foreign output rejection, entity validation, provider privacy, and no automatic retries passed |
| Clarification and model controls | No action execution on clarification or low confidence; root/nested clarification context; agent LLM switch; shared model quota; failed-turn retention checks passed |
| Hosted embeddings and cache | Ordered/normalized vectors, invalid-vector rejection, cosine dimensions, agent/revision/language/content cache isolation, and offline local-load backoff passed |
| Real local embedding smoke checks | BGE small English model loaded through FastEmbed/ONNX on CPU; balance paraphrase matched at approximately 0.7301 cosine; unrelated query rejected |
| Actual API with real embeddings and isolated database | Authenticated signup/setup, saved workflow, semantic selection, saved Send message execution, persisted matching state, and unrelated-message rejection passed |
| Angular component state checks | Existing editor/tester interactions passed, including step numbers, entity-result acceptance and replacement, draft handling, variable inspector, and server language propagation |
| Set variable editor/tester state checks | Action creation and editing, invalid input, draft retention, failed-save retention, variable inspection, and propagation of the server's active language passed |
| Database/dependency upgrade | No new migration; new Groq/FastEmbed/ONNX dependencies installed and imported successfully on Python 3.9.12 |
| Python 3.9 source parsing for backend modules and project scripts | Passed |
| Sample Petrix JSON agent import | Existing import API tests passed; imported agents also receive starter workflows |
| Local PostgreSQL configuration helper, including Windows encoding normalization | Previously passed under Python 3.9.12; helper unchanged |
| Platform plan helper, capacity update, and rejection of a downgrade below usage | Previously passed under Python 3.9.12; helper unchanged |
| Runtime dependencies | Installed environment passes `uv pip check`; updated lock resolves for Linux and Windows x64 targeting exact Python 3.9.12 |

The database checks used a PGlite PostgreSQL WebAssembly engine exposed through the PostgreSQL wire protocol to psycopg and SQLAlchemy. Tests applied the actual migrations in isolated schemas and executed real queries, transactions, constraints, and API endpoints. The deliverable uses ordinary PostgreSQL and has no PGlite dependency. Runtime checks ran on Linux with exact Python 3.9.12; Windows execution itself was not performed here.

The 281 tests retain all 239 preceding cases and add 42 provider and layered-matching cases. These exercise the actual Groq SDK with controlled HTTP responses, embedding adapters/cache, stage selection, allowed candidates, clarification, entity access, context, quotas, provider failures, and nested continuation handling. Existing expression, session-variable, numbering, ordering, permissions, CSRF, isolation, history-retention, and authentication checks also pass. The real embedding/API smoke check is additional to this automated suite and uses no Groq credentials.

The generative adapter retains its mocked success/failure checks. The new intent adapter uses a separate routing prompt, current-level candidate allow-list, and strict JSON schema; its SDK/transport contract was checked with mocked Groq responses. No live Groq request was made because no user API key was available. The local embedding model was downloaded and executed, including through the actual authenticated event API. These smoke checks establish integration behaviour, not a general intent-recognition accuracy benchmark.

The Angular production build passed with an initial bundle of about 291 kB raw and a dashboard lazy chunk of about 177 kB raw. Its strict compiler verified the layered-matching settings, matching diagnostics, numbered workflow list, recursive tree template, variable inspector, and tester contracts. Existing bundle/component-style budgets pass. Component-state checks instantiate the actual Angular editors and tester with a controlled API provider; these cover interaction state rather than rendered browser layout.

## Previous remaining environment checks

- Native PostgreSQL 18 concurrent traffic was not exercised. Workspace row locks and revision checks protect capacity and updates; concurrent native-server testing remains part of deployment validation.
- An interactive browser walkthrough and rendered-screen inspection were not performed here. A browser download was unavailable in this environment. The production build and editor/tester state checks passed; test desktop/mobile layout and focus behavior in your local browser after applying the update.
- Local Windows execution, Docker launch, and live SMTP delivery were not performed in this check. Your existing local PostgreSQL and Mailpit setup can be reused.
- Live Google/Microsoft authentication, Groq intent recognition, and generative replies require your configured provider credentials. Model response/error contracts were tested locally with mocked transport; local semantic inference was exercised with a real model.
- Voice runtime, action handlers beyond Send message, Set variable, and Jump, knowledge retrieval, channel publishing, Co-pilot, and subscription-payment automation are subsequent integrations. Attachment and timeout events are explicitly simulated tester inputs; no channel delivery or external action completion is claimed.

Follow [INTENT_MATCHING.md](INTENT_MATCHING.md) to install the new dependencies, configure the Groq key, prepare embeddings, and restart the services. Migration `0004` is the schema head. The complaint-reference example remains in [SET_VARIABLE.md](SET_VARIABLE.md).
