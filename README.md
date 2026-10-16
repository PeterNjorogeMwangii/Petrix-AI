# Petrix AI — Workflows, Send message, Set variable, and Jump

A new codebase for a conversational AI platform. The application implements passwordless account creation, workspace onboarding, AI agent management, a persisted workflow builder, and a conversation tester that executes saved Send message and Set variable actions. Version `0.4.0` adds fuzzy → semantic embeddings → Groq intent recognition while preserving permanent step numbers. See [docs/INTENT_MATCHING.md](docs/INTENT_MATCHING.md) for setup and [docs/SET_VARIABLE.md](docs/SET_VARIABLE.md) for the complaint-reference example.

Version `0.4.1` adds **Jump**: choose any saved trigger or action in this agent's workflow, search by name or permanent number, and redirect the conversation to that step. See [docs/JUMP.md](docs/JUMP.md) for setup, execution rules, and the return-to-menu example. This update adds no dependency or migration; schema head remains `0004`. Copy the updated source into your existing project, keep your `backend/.env`, and restart the API and Angular.

## Stack and compatibility

| Layer | Implementation |
| --- | --- |
| Frontend | Angular 21.2, standalone components, reactive forms, lazy routes |
| Backend runtime | Python 3.9.12, verified with that exact interpreter |
| API | FastAPI 0.128.8 |
| Database | PostgreSQL through SQLAlchemy 2 and psycopg 3.2 |
| Authentication | Authlib 1.6.12, email magic links, revocable cookie sessions |
| Schema changes | Alembic 1.16.5, frozen migrations `0001` through `0004` |

The compatibility update replaces Python 3.10 union annotations with `typing.Optional` and pins runtime dependencies, including their transitive dependencies, to versions that install on Python 3.9.12. Windows-specific dependencies use platform markers. Version `0.4.0` adds Python 3.9-compatible Groq SDK and CPU embedding dependencies. Migration `0004` remains the schema head.

Use the revised `requirements.lock.txt` when installing. Updating only the old FastAPI pin is insufficient because other original dependencies also require a newer Python version.

## Upgrade your running installation

Follow [docs/INTENT_MATCHING.md](docs/INTENT_MATCHING.md). Copy the updated source over your existing project, keep `backend/.env` and your virtual environment, and install `backend/requirements.lock.txt`. Add `GROQ_API_KEY` to your existing environment file and prepare the local embedding model. Run `python -m alembic upgrade head` **from the backend directory** before restarting the API and Angular. Schema head remains `0004`; existing accounts, workspace settings, workflows, step numbers, and chat history are retained.

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
- Send message with up to 10 random message variants, 13 quick replies, manual language translations, and agent-name placeholders.
- Bold assistant text using `**text**`, including substituted variables such as `**{company}**`.
- Workflow execution in the tester, including nested waits, fallback matching, simulated attachment/closure/CSAT events, and execution traces.
- A saved Chat started → Send message greeting for existing and newly created/imported agents.
- Set variable with a dedicated editor, bounded Python expressions, typed session data, system-variable reads, and a tester variable inspector.
- Custom variable placeholders in messages and quick replies, conversation persistence, and transactional error handling.
- Jump with a searchable trigger/action picker, permanent destination numbers, selected-target card, cross-branch execution, destination waits/continuations, reference validation, and loop protection.
- Individual backend folders for all 19 action types. Send message, Set variable, and Jump are implemented; the other catalogue entries are reserved for later configuration.
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
