# Petrix AI — LLM Processing and Workflows

A new codebase for a conversational AI platform. The application implements passwordless account creation, workspace onboarding, AI agent management, a persisted workflow builder, and a conversation tester that executes saved Send message, Send attachment, Show carousel, Ask survey, Collect feedback, Set variable, Jump, Branch, Switch Language, Close chat, Send API, SQL Query, and LLM Processing actions. Version `0.4.0` adds fuzzy → semantic embeddings → Groq intent recognition while preserving permanent step numbers. See [docs/INTENT_MATCHING.md](docs/INTENT_MATCHING.md) for setup and [docs/SET_VARIABLE.md](docs/SET_VARIABLE.md) for the complaint-reference example.

Version `0.4.1` adds **Jump**: choose any saved trigger or action in this agent's workflow, search by name or permanent number, and redirect the conversation to that step. See [docs/JUMP.md](docs/JUMP.md) for setup, execution rules, and the return-to-menu example. That Jump release added no migration. Later releases add more actions and storage migrations; follow the current upgrade steps below.

Version `0.5.0` adds **Ask survey** with sequential Text, Number, Email, Phone, Attachment, and Attachment(s) questions, quick answers, skip, typed variables, validation replies, and actual file uploads/downloads in the tester. See [docs/ASK_SURVEY.md](docs/ASK_SURVEY.md). Run `python -m alembic upgrade head` **from backend** to apply migration `0005` before restarting the API. Keep your existing environment files. No new dependency is introduced by this survey update.


Version `0.6.0` adds **Branch** with up to 10 ordered Python conditions, separate action paths, Else, survey `.text` / `.norm.value` views, nested waits, and path-aware Jump targets. See [docs/BRANCH.md](docs/BRANCH.md) for configuration and continuation rules. No new dependency or migration is required; that release used schema head `0005`.

Version `0.7.0` adds **Send API**: GET, POST, PUT, PATCH, and DELETE over HTTPS, configurable headers, JSON or Python payloads, response variables, and separate Success/Error/Timeout paths. See [Send API](docs/SEND_API.md). Existing dependencies and migrations are unchanged; that release used schema head `0005`.

Version `0.8.0` adds **SQL Query** and **Settings → Teamspace → Database connections**: verified PostgreSQL connectors, encrypted credentials, parameterized read-only SELECTs, bounded result variables, and Success/Error/Timeout paths. All Settings sections are present; only Database connections and the existing Roles & permissions page are functional. See [SQL Query](docs/SQL_QUERY.md) and [Database connections](docs/DATABASE_CONNECTIONS.md). Apply migration `0006` using the upgrade steps below.

Version `0.9.0` adds **Switch Language**: choose a configured target language, preserve it through subsequent actions and chat turns, match translated triggers, and direct generative fallback prompts to that language. See [Switch Language](docs/SWITCH_LANGUAGE.md). Language removal/version restore protects saved switch targets. That release added no dependency or migration and used schema head `0006`.

Version `0.10.0` adds **Send Attachment**: image/audio/video/document uploads up to 20 MiB, hosted file links, safe Python URL expressions, separate content for each language, authenticated previews/downloads, and immutable sent copies. See [Send Attachment](docs/SEND_ATTACHMENT.md). Apply migration **0007** with `python -m alembic upgrade head` from backend before restarting. Existing survey upload limits remain unchanged.

Version `0.11.0` adds **Show Carousel**: 1–10 localised cards, optional uploaded/hosted images, titles/subtitles, up to three reply/link buttons per card, card reordering, and embedded webpage views in the tester. Reply buttons use existing workflow triggers; webview links include an external-tab option. See [Show Carousel](docs/SHOW_CAROUSEL.md). No new migration or dependency is introduced; schema head remains `0007`.

Version `0.12.0` adds **Chat Closed** post-conversation restrictions and **Collect Feedback**: a permanent close trigger, a restricted action catalogue and Jump destinations, localised rating titles, a one-time 1–5 feedback panel, and closure/rating persistence even if subsequent automation fails. Test the lifecycle with **Test an event → Chat closed (live-agent event)**. See [Chat Closed](docs/CHAT_CLOSED.md). Livechats and published-channel close integration remain later milestones. No new migration or dependency is required; schema head stays `0007`.

Version `0.13.0` completes **Collect Feedback under Chat Closed** with Rating, Survey, and Settings tabs, configurable rating variables, required/optional typed survey fields, localised questions/placeholders/choices/confirmations, vector rating faces, and channel-specific tester previews. Feedback now pauses the following actions until submission, saves the answer variables, and then resumes the workflow. Place closure logs before it when they must run without a submission. See [Collect Feedback](docs/COLLECT_FEEDBACK.md) and the combined [Chat Closed flow](docs/CHAT_CLOSED.md). No new migration or dependency is required; head remains `0007`.

Version `0.14.0` activates **Close chat** in **Add action → Flow control**, marked Ready. It stops the open workflow, commits the conversation closure, and invokes Chat Closed once, including configured Collect Feedback. Already-closed conversations continue without another closure event. Earlier messages/files survive post-close automation errors. See [Close Chat](docs/CLOSE_CHAT.md). No new migration or dependency; head remains `0007`.


Version `0.15.0` adds **LLM Processing** with text/Python input and system prompts, the configured Groq intent model, temperature, agent Knowledge context, direct text response variables, and Success/Error/Timeout paths. The **Knowledge** tab now saves enabled text sources for each agent. See [LLM Processing](docs/LLM_PROCESSING.md) for a complaint-summary flow and [Knowledge](docs/KNOWLEDGE.md) for setup. Apply migration **0008** from backend and restart the API after setting `GROQ_API_KEY` and `GROQ_INTENT_MODEL=openai/gpt-oss-20b`. Existing dependency pins are unchanged.

## Stack and compatibility

| Layer | Implementation |
| --- | --- |
| Frontend | Angular 21.2, standalone components, reactive forms, lazy routes |
| Backend runtime | Python 3.9.12-compatible source; latest update tested on Python 3.12.14 |
| API | FastAPI 0.128.8 |
| Database | PostgreSQL through SQLAlchemy 2 and psycopg 3.2 |
| Authentication | Authlib 1.6.12, email magic links, revocable cookie sessions |
| Schema changes | Alembic 1.16.5, frozen migrations `0001` through `0008` |

The compatibility update replaces Python 3.10 union annotations with `typing.Optional` and pins runtime dependencies, including their transitive dependencies, to versions that install on Python 3.9.12. Windows-specific dependencies use platform markers. Version `0.4.0` adds Python 3.9-compatible Groq SDK and CPU embedding dependencies. Migration `0008` is the schema head and adds agent Knowledge text sources; `0007` adds workflow media storage; `0006` adds encrypted workspace database connectors; `0005` adds conversation file storage.

Use the revised `requirements.lock.txt` when installing. Updating only the old FastAPI pin is insufficient because other original dependencies also require a newer Python version.

## Upgrade your running installation

Follow [docs/INTENT_MATCHING.md](docs/INTENT_MATCHING.md). Copy the updated source over your existing project, keep `backend/.env` and your virtual environment, and install `backend/requirements.lock.txt`. Add `GROQ_API_KEY` to your existing environment file and prepare the local embedding model. Run `python -m alembic upgrade head` **from the backend directory** before restarting the API and Angular. Schema head is `0008`; existing accounts, workspace settings, workflows, step numbers, and chat history are retained.

## Implemented features

- Personal and work email registration, including Gmail and Yahoo.
- Passwordless authentication through single-use, expiring magic links.
- Automatic account/workspace creation with an Owner membership after verification.
- Reuse of the same user and workspace on subsequent sign-ins.
- Workspace name, industry, country, optional website, and optional image avatar.
- A protected dashboard with a workspace agent selector, creation/import dialogs, and separate configuration sections.
- A Default AI Agent for each new workspace and a one-time backfill for existing workspaces.
- Persona, LLM toggle, supported/default languages, names, and PNG/JPEG/GIF avatars up to 10 MiB.
- Backend permission checks and configurable workspace plan capacity.
- Settings navigation for all supplied sections, with working verified PostgreSQL Database connections and planned pages for future settings.
- SQL Query with SELECT-only validation, bound chat parameters, read-only transactions, timeouts, response variables, and three outcome paths.
- Settings → Workspace → Roles & permissions for administrator/member grants.
- Immutable configuration versions, restoration, and change history.
- Directed agent relationships in the Network tab.
- Petrix JSON export/import and permanent agent deletion with retained conversation history.
- Saved voice preferences and an optional real model-connected conversation tester.
- A workflow list, nested trigger/action trees, explicit insertion points, reorder controls, enable/disable controls, and per-step settings.
- Permanent numbers for every saved trigger and action, shown in lists, nested trees, settings, and tester traces. Numbers survive renaming and reordering, and deleted numbers are not reused.
- Layered Message received matching: fuzzy text, real semantic embeddings with cosine similarity, and structured Groq intent recognition using `openai/gpt-oss-20b`.
- Ambiguity margins, confidence thresholds, clarification questions, recent-context recognition, extracted entities, and tester matching diagnostics. Exact-only triggers remain supported.
- Permanent Chat started, Chat closed, Chat timeout, and AI timeout triggers; configurable Message received, Attachment received, Fallback, and Collect feedback triggers.
- Chat Closed action restrictions across descendants and Jump continuations, plus a localised Collect Feedback action with Rating/Survey/Settings, persisted answer variables, post-submission continuation, channel previews, and durable lifecycle status.
- Ready Close chat action with terminal open-flow behavior, one-time Chat Closed dispatch, idempotence after closure, and preserved pre-close messages/media on automation failure.
- Send message with up to 10 random message variants, 13 quick replies, manual language translations, and agent-name placeholders.
- Bold assistant text using `**text**`, including substituted variables such as `**{company}**`.
- Workflow execution in the tester, including nested waits, fallback matching, simulated attachment/closure/CSAT events, and execution traces.
- A saved Chat started → Send message greeting for existing and newly created/imported agents.
- Set variable with a dedicated editor, bounded Python expressions, typed session data, system-variable reads, and a tester variable inspector.
- Custom variable placeholders in messages and quick replies, conversation persistence, and transactional error handling.
- Jump with a searchable trigger/action picker, permanent destination numbers, selected-target card, cross-branch execution, destination waits/continuations, reference validation, and loop protection.
- Ask survey with 1–50 fields, all six answer formats, up to 13 quick answers per question, skip, typed session variables, protected file uploads/downloads, and exact branch continuation.
- Branch with named If/Else-if conditions, an Else path, condition reordering, independent nested actions/triggers, safe Python expressions, survey-value views, and selected-path execution traces.
- Send API with five HTTPS methods, variable-aware headers/URLs, JSON and Python payloads, response variables, Success/Error/Timeout paths, verified TLS, bounded responses, and private-host configuration.
- Show Carousel with localised cards, image uploads, reply/link buttons, horizontal navigation, and in-chat webviews.
- Send Attachment with uploads, hosted links, safe Python URL expressions, per-language files, tester previews, and downloads.
- Switch Language with configured-language selection, persistent chat language, translated message/trigger continuation, and execution trace.
- Individual backend folders for all 20 action types. Send message, Send attachment, Show carousel, Ask survey, Collect feedback, Set variable, Jump, Branch, Switch Language, Close chat, Send API, and SQL Query are implemented; the other catalogue entries are reserved for later configuration.
- Session revocation on sign-out.
- Google and Microsoft OpenID Connect integration when backend credentials are configured.

Knowledge ingestion, additional action handlers, live voice channels, inbox, publishing, analytics, billing automation, and Co-pilot are subsequent milestones. Saved Send message workflows work without an LLM. A configured compatible model may answer unmatched text when the agent's LLM flag is enabled; matching workflows and configured fallbacks take priority. The greeting is executed from saved workflow data. Default workspace capacity is three agents and three languages per agent, and can be changed by the platform administrator. See [docs/WORKFLOWS.md](docs/WORKFLOWS.md) and [docs/AI_AGENTS.md](docs/AI_AGENTS.md).

## Run services separately on Windows

You can use your existing Python 3.9.12 virtual environment. Docker is optional.

1. Extract the updated source into your project folder. The archive excludes `.env`, virtual environments, `node_modules`, and database data.
2. Create the dedicated PostgreSQL database `petrix` if you have not already done so. In pgAdmin, connect the Query Tool to `postgres` and run:

```sql
CREATE DATABASE petrix;
```

If you already created `petrix`, continue with configuration. Tables are created by Alembic.

3. Open PowerShell in `C:\Users\Admin\petrix-ai`, with your existing virtual environment active:

```powershell
python --version
python -m pip install -r .\backend\requirements.lock.txt
```

The version should be Python 3.9.12. If creating a new environment from scratch, use a Python 3.9 interpreter to create `.venv`, then activate it before installing.

4. Copy the backend configuration template if you do not already have `backend/.env`:

```powershell
Copy-Item .\backend\.env.example .\backend\.env
```

Edit `DATABASE_URL` in `backend/.env` with your PostgreSQL username and password:

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/petrix
```

URL-encode special characters in the username or password, for example `@` as `%40`, `#` as `%23`, and `%` as `%25`. Then verify the connection:

```powershell
python .\scripts\configure_local.py
```

The helper displays the exact path it reads, loads `DATABASE_URL` from `backend/.env` without prompting, checks connectivity, and generates a signing secret if it is missing or too short. It preserves your other settings and any existing valid signing secret. Windows UTF-8 and UTF-16 files with a byte-order mark are accepted and saved as ordinary UTF-8 after a successful connection so the backend can read them. Run it from any working directory; it always reads the backend configuration beside the source. It does not create the database or tables.

5. Apply the schema and start FastAPI:

```powershell
cd backend
python -m alembic upgrade head
python -m uvicorn app.main:create_app --factory --reload --port 8000 --no-access-log
```

Keep that terminal running. Open http://localhost:8000/docs and http://localhost:8000/api/health. The health response should report `status: ok`.

6. In a second PowerShell terminal, start Angular. Use Node.js 24 LTS or Node.js 22.12+:

```powershell
cd C:\Users\Admin\petrix-ai\frontend
node --version
npm ci
npm start
```

Open http://localhost:4200. Angular proxies `/api/` requests to FastAPI. Consistently use `localhost` in the browser and OAuth URLs.

## Email verification setup

Starting the API and frontend does not start an SMTP service. Completing email sign-up needs SMTP configured in `backend/.env`.

For local testing, run a standalone Mailpit instance on SMTP port 1025, with its inbox at http://localhost:8025. Mailpit captures mail locally; it does not send to your real Gmail inbox. See the [official Mailpit installation instructions](https://mailpit.axllent.org/docs/install/).

For an actual email inbox, configure your SMTP server, port, username, password, and sender. Use `SMTP_SSL=true` for implicit TLS or `SMTP_STARTTLS=true` for STARTTLS, depending on the server. Do not enable both. Restart FastAPI after changing environment configuration. The backend reports a delivery failure if no SMTP service is reachable; it does not bypass verification.

## Google and Microsoft sign-in

Provider buttons become available only when their backend client ID and secret are configured. Secrets belong in `backend/.env` for local runs.

| Provider | Local callback URL |
| --- | --- |
| Google | http://localhost:8000/api/auth/oauth/google/callback |
| Microsoft | http://localhost:8000/api/auth/oauth/microsoft/callback |

See [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md) for registration details and account-association policy. Google Gmail can associate with a matching verified Petrix account. Microsoft and non-authoritative Google email claims require mailbox verification on first association. Later provider logins use the stable issuer and subject.

## Optional Docker setup

If you later use Docker, initialize its development configuration with `python scripts/init_dev.py`, then run `docker compose up --build`. The local-only Compose services are frontend, backend, PostgreSQL, and Mailpit. The Docker database is on host port 5434; local Windows PostgreSQL is normally on 5432.

The root `.env` is for Compose. `backend/.env` is for local Python. The configuration helper for separate services is `configure_local.py`; the Docker helper is `init_dev.py`.

## Verification

Frontend build:

```powershell
cd frontend
npm run build
```

Backend tests apply the real migration inside a randomly named test schema. They remove only their own schema afterwards. Use a dedicated test database and an account allowed to create/drop schemas:

```powershell
cd backend
python -m pip install -r requirements-dev.txt
$env:TEST_DATABASE_URL = "postgresql+psycopg://YOUR_TEST_USER:YOUR_TEST_PASSWORD@localhost:5432/YOUR_TEST_DATABASE"
python -m pytest -q
```

Database tests skip when `TEST_DATABASE_URL` is absent. See [docs/VERIFICATION.md](docs/VERIFICATION.md) for preparation checks and limits.

## Source layout

| Folder | Purpose |
| --- | --- |
| `frontend/src/app/core` | API client, account state, route protection, typed contracts |
| `frontend/src/app/features/auth` | Authentication screens and provider association |
| `frontend/src/app/features/onboarding` | Workspace setup |
| `frontend/src/app/features/dashboard` | Dashboard shell |
| `frontend/src/app/features/workflows` | Workflow builder, typed API, and event-driven tester |
| `frontend/src/app/features/workflows/actions/send-message` | Send message configuration component |
| `frontend/src/app/features/workflows/actions/set-variable` | Set variable configuration component |
| `frontend/src/app/shared` | Branding, icons, authentication layout |
| `backend/app/core` | Configuration and token helpers |
| `backend/app/routes` | Authentication and workspace endpoints |
| `backend/app/auth_service.py` | Registration, association, sessions, rate limits |
| `backend/app/models.py` | Database models |
| `backend/migrations` | Database migration history |
| `backend/tests` | API and identity validation tests |
| `scripts/configure_local.py` | Local PostgreSQL connection check using `backend/.env` |
| `scripts/set_workspace_plan.py` | Platform administrator helper for workspace capacity |
| `backend/app/agent_service.py` | Agent persistence, permissions, quota checks, snapshots, avatars |
| `backend/app/routes/agents.py` | Agent management and tester APIs |
| `backend/app/routes/workflows.py` | Workflow editing and tester-event APIs |
| `backend/app/workflows` | Workflow validation, defaults, and bounded runtime |
| `backend/app/workflows/actions/<action_type>` | Individual action modules; Send message has its own schema and handler |
| `backend/app/workflows/chat_script.py` | Bounded Python expression interpreter |
| `backend/app/workflows/context.py` | Conversation variables and supported system variables |
| `examples` | A validated Petrix agent import example |
| `docs` | API, authentication policy, scope, schema, verification |

## Deployment boundary

This build targets your current local Python 3.9.12 environment. Python 3.9 reached end of life on 31 October 2025; future public deployment should revisit runtime support. See the [Python release support status](https://devguide.python.org/versions/). The compatible dependency lines are deliberately pinned; newer releases may drop Python 3.9.

Public deployment is a separate milestone. The initial Terms and Privacy pages describe their development-preview status. Existing HTTPS, Secure-cookie, encrypted-SMTP, membership, CSRF, and session rules remain enforced by the backend.

## References

- [FastAPI release notes: Python 3.9 support ended in 0.129.0](https://fastapi.tiangolo.com/release-notes/)
- [Angular version compatibility](https://angular.dev/reference/versions)
- [Google backend identity validation](https://developers.google.com/identity/sign-in/web/backend-auth)
- [Microsoft OpenID Connect protocol](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc)
