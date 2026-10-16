# Petrix AI — Manage AI Agents

Milestone 2 adds persisted AI-agent configuration to the existing passwordless account and workspace flow. The dashboard uses the supplied screenshots as layout references while retaining Petrix branding.

## Upgrade your existing Windows installation

1. Stop the backend and Angular terminals with Ctrl+C. Mailpit can keep running.
2. Extract the updated source and copy its contents into `C:\Users\Admin\petrix-ai`. Keep your current `backend/.env`, root virtual environment, database, and Mailpit executable. The source archive excludes those items.
3. Run the migration from the backend directory with your virtual environment active:

```powershell
cd C:\Users\Admin\petrix-ai\backend
python -m alembic upgrade head
python -m uvicorn app.main:create_app --factory --reload --port 8000 --no-access-log
```

Migration `0002` adds the agent tables, permissions, and workspace plan capacity. It inserts one Default AI Agent into every existing workspace, with a first version and change-log entry. New registrations create their default agent in the same transaction as account/workspace creation. Listing agents and returning sign-ins do not recreate a deleted default agent. Existing authentication and workspace data are retained.

4. Start Angular in a second terminal:

```powershell
cd C:\Users\Admin\petrix-ai\frontend
npm ci
npm start
```

Use `http://localhost:4200` consistently. The start script binds to `localhost` to match the configured request origin. Complete email sign-in using your Mailpit inbox at `http://localhost:8025`, or reuse your existing session.

Python 3.9.12 remains the backend runtime. Version `0.4.0` adds pinned Groq and embedding dependencies; follow [INTENT_MATCHING.md](INTENT_MATCHING.md) when upgrading to the current source.

## Agent management

The top selector lists real agents from your workspace. Its Create AI agent action opens New AI agent and Import AI agent choices. Your selection is reflected in the URL and remembered for that workspace during the browser session.

Creation accepts a name, default language, and optional avatar. Avatar uploads accept actual PNG, JPEG, or GIF data up to 10 MiB and 16 million source pixels. Images are re-encoded as 256×256 PNGs without source metadata; animated GIFs use the first frame.

The Agent tab separates Personalization, Languages, Settings, and Danger zone. Each section has its own Save button and draft state. Saving one section keeps drafts in other sections. Switching agents, reloading, or leaving with unsaved changes requires a discard confirmation. A stale revision returns 409, allowing the user to reload rather than overwrite another editor's saved changes.

The LLM toggle controls Groq intent recognition and optional generative replies for unmatched text. Exact/fuzzy matching, configured semantic embeddings, and saved actions work when it is off. Supported workflow languages are selected from the language catalog; the default must be a selected language. See [WORKFLOWS.md](WORKFLOWS.md) and [INTENT_MATCHING.md](INTENT_MATCHING.md).

## Import and export

Import accepts a Petrix JSON export up to 15 MiB. It creates a new agent after server-side validation and capacity checks. Exports include saved identity, persona, languages, voice preferences, and the sanitized avatar, using this contract:

```json
{
  "format": "petrix-agent",
  "schema_version": 1,
  "agent": {
    "name": "Banking assistant",
    "llm_enabled": true,
    "persona": "Provide clear, professional banking support.",
    "languages": ["en", "sw"],
    "default_language": "en",
    "voice_enabled": false,
    "voice_name": "",
    "voice_speed": 1.0,
    "avatar_base64": null
  }
}
```

Exports omit workspace/user IDs, credentials, versions, chat logs, and external relationship IDs. Imports cannot set ownership or capacity. Proto exports require a format-specific conversion; no Proto compatibility is claimed without a real export schema. A sample Petrix JSON file is included in `examples/banking-assistant.petrix.json`.

## Permissions and workspace capacity

Owners always retain Manage AI Agents. Administrators have it by default; members initially have view access. Owners and administrators can grant or revoke the permission for the Administrator and Member roles under Settings → Workspace → Roles & permissions. Agent write operations, export, and tester messages check this permission on the backend. All writes require session authentication, CSRF validation, and a permitted origin. Foreign workspace and agent identifiers return 404.

The initial Starter capacity is three agents and three languages per agent. The plan label and limits are stored on each workspace. `DEFAULT_AGENT_LIMIT` and `DEFAULT_LANGUAGE_LIMIT` configure new workspaces. Subscription automation and payments are a later milestone.

A platform administrator with local database access can update an existing workspace using this helper from the project root:

```powershell
python .\scripts\set_workspace_plan.py WORKSPACE_UUID --plan "Professional" --agents 10 --languages 5
```

The helper rejects a downgrade below current usage. Workspace owners cannot raise these limits through the public API. Create/import operations lock the workspace row while checking and consuming capacity. Delete and configuration changes use the same lock order. Native PostgreSQL concurrency stress tests remain an environment check.

## Versions and change log

Creation/import starts at version 1. A saved configuration change creates an immutable snapshot and change-log entry; an unchanged save does not create a duplicate version. Version snapshots include the sanitized avatar. Restoring earlier settings applies the current language quota and creates a new version if the configuration changes. Later history remains available. Version and change-log lists support pagination.

Network changes and role-permission changes are recorded in the audit table. Network stores directed relationships between agents in the same workspace. The Network tab shows incoming and outgoing links, with add/remove actions. Workflow handoff execution and channel routing are subsequent milestones.

Deletion requires the current revision and the exact agent name. It permanently removes configuration, avatar, versions, and network links. Historical tester conversations/messages and audit records retain the agent's recorded name with a null agent reference. Live channel publishing and audience migration are future features; their deletion integration must preserve this retention policy.

## Voice

The Voice tab saves an enable flag, preferred voice identifier, and speaking speed (0.5×–2×), and includes them in version snapshots and exports. Live synthesis, speech recognition, voice-provider discovery, and voice channels require later integrations. The UI indicates that no voice channels are connected.

## Optional conversation tester

The tester's initial greeting is executed from the saved Chat started → Send message action. For the Groq intent-recognition stage, configure `GROQ_API_KEY` as described in [INTENT_MATCHING.md](INTENT_MATCHING.md). For optional generative answers to unmatched text, configure a separate OpenAI-compatible chat-completion connection in `backend/.env`, then restart FastAPI:

```dotenv
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=YOUR_PROVIDER_API_KEY
LLM_MODEL=YOUR_AVAILABLE_CHAT_MODEL_ID
LLM_TIMEOUT_SECONDS=30
LLM_MAX_TOKENS=1024
LLM_TESTER_REQUESTS_PER_MINUTE=20
```

The Groq base URL and chat endpoint are documented in [Groq's compatibility guide](https://console.groq.com/docs/openai) and [API reference](https://console.groq.com/docs/api-reference). Select a chat model currently available in your provider account. Other compatible providers or a local model server can use their own base URL and model ID; local HTTP connections are permitted in development. Production connections require HTTPS.

The generative adapter supplies the saved persona and supported/default languages, and retains up to 20 recent messages as model context. The intent classifier uses a separate routing prompt and at most six recent messages. API keys remain on the backend. Generative-provider failures return a generic 503; failed turns are not added to conversation history. Intent-provider failures can reach a configured workflow fallback and produce controlled tester diagnostics. User/agent ownership and CSRF checks protect conversations, and a shared per-user model request limit returns 429 when exceeded. Resetting the tester starts a new conversation and replays Chat started; previous chat records are retained. Disabling the LLM stops both model stages while exact, fuzzy, semantic, and saved-action execution remain available.

Knowledge retrieval, action handlers beyond Send message and Set variable, channel publishing, and Co-pilot are subsequent milestones. The existing agent import/export format and Agent Versions tab cover agent settings. Workflow definitions and their revision counter are separate; settings restoration does not change workflow trees. See [SET_VARIABLE.md](SET_VARIABLE.md) for session data and expressions.
