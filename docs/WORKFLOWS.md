# Petrix AI — Workflows, Ask survey, Send message, Set variable, and Jump

Milestone 3 implements the workflow builder, its persistence and runtime, and Send message. The next update adds Set variable with typed session data and message placeholders; see [SET_VARIABLE.md](SET_VARIABLE.md). Other action types have individual reserved backend folders and remain unavailable until their configuration and handlers are implemented.

Version `0.4.0` adds layered intent matching. Follow [INTENT_MATCHING.md](INTENT_MATCHING.md) to install the new dependencies, add the Groq key, and prepare local embeddings before using the additional stages.

Version `0.4.1` implements Jump. Select an existing trigger or action from the searchable numbered tree picker, then Save. The runtime leaves the source branch and resumes at the destination, including its nested waits and enclosing action continuations. Targets use UUIDs so renaming and reordering keep references intact. See [JUMP.md](JUMP.md). No new migration or dependency is required.

Version `0.5.0` adds Ask survey and real file answers. See [ASK_SURVEY.md](ASK_SURVEY.md). It uses migration `0005` to store uploaded bytes with their conversation; existing workflow definitions, numbers, and data are retained.

## Upgrade on Windows

Stop the API and Angular terminals with Ctrl+C. Copy the updated `petrix-ai` source contents into `C:\Users\Admin\petrix-ai`. Keep your current `backend/.env`, `.venv`, PostgreSQL database, and Mailpit installation.

In PowerShell, activate the environment belonging to Petrix before migrating:

```powershell
cd C:\Users\Admin\petrix-ai
.\.venv\Scripts\Activate.ps1
python --version
cd .\backend
python -m alembic upgrade head
python -m alembic current
```

Use Python 3.9.12. The migration check must show `0005 (head)` before starting the API. Migration `0003` adds a workflow document for each existing agent and workflow state/metadata to tester chats. Migration `0004` numbers existing triggers and actions and adds a counter per agent. Migration `0005` adds file storage. These migrations retain existing action configuration. Deleted greetings stay deleted, and accounts and agent settings remain intact.

```powershell
python -m uvicorn app.main:create_app --factory --reload --port 8000 --no-access-log
```

In a second PowerShell terminal:

```powershell
cd C:\Users\Admin\petrix-ai\frontend
npm ci
npm start
```

For version `0.4.0`, first install the updated dependency lock file and complete the provider setup in [INTENT_MATCHING.md](INTENT_MATCHING.md). Open `http://localhost:4200` and reuse your current session or sign in through Mailpit.

## Configure the greeting first

1. Select your agent and open **Workflows**.
2. Open **Chat started** and select its **Send message** row.
3. Edit the saved message, then select **Save**.
4. Restart the tester using its refresh button.

The starter action stores `Hi! I’m {_chatbot.name}. How can I assist you today?`. The backend replaces the name placeholder using the agent's saved name. No greeting is hardcoded in the Angular tester. Renaming the agent updates the rendered placeholder; disabling or deleting the action stops that message. New agents and agents imported using the existing settings format receive the same starter workflows.

## Triggers and tree rules

### Permanent step numbers

Every saved trigger and action has a number such as **#1** or **#7**, including disabled steps and nested branches. The same number appears in the workflow list, tree, settings panel, and tester execution details. It belongs to that step and stays the same when you rename, move, edit, enable, or disable it. A number identifies a step; displayed tree order determines execution order.

Each agent has its own sequence shared by triggers and actions. New agents start with Chat started **#1**, Chat closed **#2**, Chat timeout **#3**, AI timeout **#4**, Fallback **#5**, and the starter Send message **#6**. The next saved step receives **#7**. Numbers are allocated when Save or Create succeeds; unsaved action drafts have no number. Deleted numbers are never reassigned.

For an existing agent, migration `0004` preserves the numbers previously displayed beside its top-level workflows, then numbers all existing descendants in tree order. It keeps names, UUIDs, configurations, workflow revisions, and saved chat variables. After this one-time upgrade, each number remains stable. Gaps after deletion are expected.

### Trigger behaviour

| Trigger | Behaviour |
| --- | --- |
| Chat started | Permanent, always enabled, top-level only. Runs when the tester starts or restarts. |
| Chat closed | Permanent, always enabled, top-level only. Runs on the explicit close event. |
| Chat timeout | Permanent, always enabled, top-level only. Available as a simulated tester event; automatic channel timers come later. |
| AI timeout | Permanent, always enabled, top-level only. Available as a simulated tester event. |
| Message received | Configurable intent examples per language; exact-only or layered fuzzy/semantic/Groq matching. Starts disabled until examples are configured. |
| Attachment received | Matches selected image, audio, video, or file categories. The current tester sends metadata rather than uploading files. |
| Fallback | One per sibling level. Runs if no enabled alternative at that level matches. The starter top-level fallback is disabled and empty. |
| Collect feedback | Runs when a closed conversation receives a CSAT rating from 1 to 5. One rating is accepted per conversation. |

Choose **New workflow** to add a top-level trigger. Enter its name and optional description, then configure it in the settings panel. Add intent examples one per line. **Layered intent matching** tries fuzzy text, then semantic cosine similarity, then structured Groq recognition. Similarity thresholds and ambiguity margins decide whether a stage accepts a candidate or continues. A default-language example set is used when the tester language has none. **Exact phrase matching** uses only normalized literal equality. See [INTENT_MATCHING.md](INTENT_MATCHING.md) for setup, context, entities, and clarification behaviour.

Actions at the same level run in displayed order. Use the Add action insertion points to place a step before the first action, between actions, or at the end. Use the up/down controls to reorder siblings of the same kind. Nested actions run before their parent's following siblings. Disabled nodes skip their entire subtree while preserving all saved settings.

Nested triggers form alternative branches after the actions at that level. Execution pauses there until a later event matches a branch or its local fallback. When that branch finishes, execution resumes the parent's remaining actions. A local fallback takes priority over top-level navigation. If there is no local match or fallback, a matching top-level workflow may interrupt the waiting branch. Workflows allow 12 nested levels, 500 nodes per agent, and a definition size of 1 MiB.

Deleting a node removes its nested steps as well; the UI asks for confirmation. Permanent system triggers cannot be deleted, disabled, duplicated, or moved below another node. Their action children can be changed, disabled, or deleted.

## Send message

- Wrap text or a variable placeholder in `**` to display it in bold in the tester, for example `Welcome to **{company}**`. Newlines and emojis are preserved; assistant text is rendered without interpreting raw HTML.
- Store 1–10 non-empty message variants, each up to 4,000 characters. One variant is chosen at random each time the action runs.
- Store up to 13 distinct, non-empty quick replies of up to 100 characters each. Clicking a current quick reply sends its text through Message received matching.
- Configure each assigned content language manually. The runtime uses that translation, then the agent's default, then the first saved translation if the default has no content yet. Stored translations remain available when agent languages change.
- Message placeholders include `{_chatbot.name}`, `{{agent.name}}`, `{{input.message}}`, and `{{feedback.rating}}`, plus session variables such as `{complaint_ref}` and structured paths such as `{{customer.name}}`. Quick replies also support variables. Substitution runs once as plain text; missing variables produce a controlled tester error.
- Each settings panel has its own Save. Unsaved step settings prompt before navigation; rejected saves preserve the current draft.

## Testing

The tester starts a real saved test conversation and executes Chat started. Exact/fuzzy matching, configured semantic matching, saved actions, and quick replies work when the agent's LLM flag is off. The flag controls Groq intent recognition and optional generative replies. Execution details show which trigger and actions ran; the Intent matching panel shows stage results and recognized entities. A matched workflow with no enabled message actions produces no artificial assistant reply. Clarification is a separate, explicitly labelled response that does not execute workflow actions.

Use **Test an event** for attachment, closure, feedback, and timeout events. Attachment testing simulates the filename/category only. Close a conversation before submitting CSAT. Closed conversations reject further messages; Restart creates a new conversation and retains previous history.

An enabled matching workflow, including a configured fallback, takes priority over generative replies. When no trigger matches, the tester may use the optional model connection if configured and enabled. Otherwise it displays a diagnostic outside the conversation rather than inventing a reply. Generative calls retain the configured per-user rate limit; tester events are limited to 60 per user per minute. Failed provider turns roll back.

Saved changes apply on the next event. A changed workflow revision clears an old nested execution context to avoid continuing deleted or reordered steps. Restart explicitly to replay Chat started after changing the greeting.

## Storage and API

Workflows require Manage AI Agents for editing and testing; read access requires workspace membership. Authentication, CSRF, completed onboarding, and workspace/agent ownership are checked on the backend. A locked workflow row and `expected_revision` reject conflicting saves with 409. Successful changes appear in the agent's Change log.

The prefix is `/api/workspaces/{workspace_id}/agents/{agent_id}`:

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/workflows` | Definition, revision, trigger definitions, and action catalogue. |
| POST | `/workflows` | JSON `expected_revision` and the complete `nodes` tree. Validates and atomically replaces the document. |
| POST | `/tester/events` | JSON `event`, `conversation_id` after start, optional `language`, and event-specific fields. Returns messages, quick replies, trace, status, and workflow revision. |

Node fields: `id` (UUID), `number` (positive integer), `kind` (`trigger` or `action`), `type`, `name`, `description`, `enabled`, `config`, and `children`. IDs and numbers remain stable through editing and reordering. The server owns numbers: saves may omit them for compatibility, and supplied numbers cannot change existing assignments or choose a new step's number. A locked per-agent counter prevents reuse after deletion. System-node IDs are checked by the backend. Future action types cannot be activated by bypassing the catalogue UI.

Tester trace entries include `node_id` and `node_number` for each matched trigger and executed action. A generative reply has both fields set to null because it is not a saved workflow step.

Workflow revisions are separate from agent-settings versions. Agent snapshot restoration and the existing `petrix-agent` import/export schema cover agent settings only. Workflow duplication, deployment snapshots, full-agent workflow import/export, and published channels are later increments. The builder labels saved revisions without claiming a channel deployment.

## Action code layout

| Location | Responsibility |
| --- | --- |
| `backend/app/workflows/actions/registry.py` | Explicit action allow-list, catalogue metadata, config/handler registration. |
| `backend/app/workflows/actions/send_message/config.py` | Send message validation and language schemas. |
| `backend/app/workflows/actions/send_message/handler.py` | Random variant selection, placeholders, and quick replies. |
| `backend/app/workflows/actions/set_variable` | Variable configuration and data-only execution handler. |
| `backend/app/workflows/actions/ask_survey` | Survey field validation, questions, and typed answers. |
| `backend/app/workflows/actions/jump` | UUID target configuration and a control-transfer result consumed by the runtime. |
| `backend/app/workflows/actions/<other_action>/__init__.py` | An individual reserved module and unavailable descriptor for each later action. |
| `frontend/src/app/features/workflows/actions/send-message` | Send message configuration UI. New action editors should get their own sibling folder. |
| `frontend/src/app/features/workflows/actions/jump` | Searchable numbered target picker and target validation. |
| `backend/app/workflows/service.py` | Tree validation and revision-checked persistence. |
| `backend/app/workflows/numbering.py` | Stable per-agent step numbers assigned during a locked save. |
| `backend/app/workflows/matching` | Fuzzy scoring, embedding adapters/cache, Groq recognition, and layered trigger selection. |
| `backend/app/workflows/runtime.py` | Trigger matching, sequential actions, nested waits, and continuations. |

Send attachment, Show carousel, Live agent handoff, External handoff, AI agent handoff, Create ticket, Branch, Switch language, Close chat, Send API, LLM processing, Auto fill field, Auto team match, SQL query, and Send graph are reserved for subsequent configuration work. No external handoff, ticket email, SQL, API action, or channel delivery is simulated as complete.
