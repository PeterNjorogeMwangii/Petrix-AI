# Petrix AI — Workflows and LLM Processing

Milestone 3 implements the workflow builder, its persistence and runtime, and Send message. The next update adds Set variable with typed session data and message placeholders; see [SET_VARIABLE.md](SET_VARIABLE.md). Other action types have individual reserved backend folders and remain unavailable until their configuration and handlers are implemented.

Version `0.4.0` adds layered intent matching. Follow [INTENT_MATCHING.md](INTENT_MATCHING.md) to install the new dependencies, add the Groq key, and prepare local embeddings before using the additional stages.

Version `0.4.1` implements Jump. Select an existing trigger or action from the searchable numbered tree picker, then Save. The runtime leaves the source branch and resumes at the destination, including its nested waits and enclosing action continuations. Targets use UUIDs so renaming and reordering keep references intact. See [JUMP.md](JUMP.md). No new migration or dependency is required.

Version `0.5.0` adds Ask survey and real file answers. See [ASK_SURVEY.md](ASK_SURVEY.md). It uses migration `0005` to store uploaded bytes with their conversation; existing workflow definitions, numbers, and data are retained.


Version `0.6.0` adds **Branch** with up to 10 ordered Python conditions, separate action paths, Else, survey `.text` / `.norm.value` views, nested waits, and path-aware Jump targets. See [BRANCH.md](BRANCH.md) for configuration and continuation rules. No new dependency or migration is required; that release used schema head `0005`.

Version `0.7.0` adds **Send API**: GET, POST, PUT, PATCH, and DELETE over HTTPS, configurable headers, JSON or Python payloads, response variables, and separate Success/Error/Timeout paths. See [Send API](SEND_API.md). Existing dependencies and migrations are unchanged; that release used schema head `0005`.

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

Use Python 3.9.12. The migration check must show `0008 (head)` before starting the API. Migration `0003` adds a workflow document for each existing agent and workflow state/metadata to tester chats. Migration `0004` numbers existing triggers and actions and adds a counter per agent. Migration `0005` adds file storage. Migration `0006` adds encrypted database connectors. Migration `0007` adds uploaded workflow assets and 20 MiB outgoing conversation-file storage. Migration `0008` adds agent Knowledge text sources. These migrations retain existing action configuration. Deleted greetings stay deleted, and accounts and agent settings remain intact.

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
| Chat closed | Permanent, always enabled, top-level only. Runs post-conversation data/feedback actions after a Ready Close chat action or the simulated live-agent close event. Conversational steps and incoming waits are forbidden, including through Jump. |
| Chat timeout | Permanent, always enabled, top-level only. Available as a simulated tester event; automatic channel timers come later. |
| AI timeout | Permanent, always enabled, top-level only. Available as a simulated tester event. |
| Message received | Configurable intent examples per language; exact-only or layered fuzzy/semantic/Groq matching. Starts disabled until examples are configured. |
| Attachment received | Matches selected image, audio, video, or file categories. The current tester sends metadata rather than uploading files. |
| Fallback | One per sibling level. Runs if no enabled alternative at that level matches. The starter top-level fallback is disabled and empty. |
| Collect feedback | Runs once when a closed conversation submits the rating and/or survey requested by a Collect Feedback action under Chat Closed. Variables are saved and the post-close continuation runs before this optional receiving trigger. |

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

The tester starts a real saved test conversation and executes Chat started. Exact/fuzzy matching, configured semantic matching, non-LLM actions, and quick replies work when the agent's LLM flag is off. The flag controls Groq intent recognition, optional generative replies, and explicit LLM Processing actions. Execution details show which trigger and actions ran; the Intent matching panel shows stage results and recognized entities. A matched workflow with no enabled message actions produces no artificial assistant reply. Clarification is a separate, explicitly labelled response that does not execute workflow actions.

Use **Test an event** for attachment, closure, feedback, and timeout events. Attachment testing simulates the filename/category only. Add a Collect Feedback action under Chat Closed before closing to issue a rating/survey form. Submit through that form; the token-bearing manual CSAT event remains available for rating-only forms. Channel preview selects Webchat faces, Messaging buttons, or typed Bitrix24/Africa’s Talking ratings, and restarts a new conversation without connecting any channel. Closed conversations reject further ordinary messages. Closure and accepted feedback persist even if subsequent automation fails; the tester reports the error. See [CHAT_CLOSED.md](CHAT_CLOSED.md).

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

Node fields: `id` (UUID), `number` (positive integer), `kind` (`trigger` or `action`), `type`, `name`, `description`, `enabled`, `config`, and `children`. Branch and Send API additionally store `paths: [{id, children}]`, matched to condition/Else IDs or Success/Error/Timeout IDs in the config; their ordinary `children` is empty. See [BRANCH.md](BRANCH.md) and [SEND_API.md](SEND_API.md). IDs and numbers remain stable through editing and reordering. The server owns numbers: saves may omit them for compatibility, and supplied numbers cannot change existing assignments or choose a new step's number. A locked per-agent counter prevents reuse after deletion. System-node IDs are checked by the backend. Future action types cannot be activated by bypassing the catalogue UI.

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
| `backend/app/workflows/actions/send_api` | Request validation, bounded HTTPS client, and outcome selection. |
| `backend/app/workflows/actions/branch` | Ordered condition configuration and path selection. |
| `backend/app/workflows/actions/jump` | UUID target configuration and a control-transfer result consumed by the runtime. |
| `backend/app/workflows/actions/<other_action>/__init__.py` | An individual reserved module and unavailable descriptor for each later action. |
| `frontend/src/app/features/workflows/actions/send-message` | Send message configuration UI. New action editors should get their own sibling folder. |
| `frontend/src/app/features/workflows/actions/jump` | Searchable numbered target picker and target validation. |
| `frontend/src/app/features/workflows/actions/branch` | Condition editor, order controls, and expression guidance. |
| `frontend/src/app/features/workflows/workflow-tree.ts` | Shared traversal through ordinary children and action outcome paths. |
| `backend/app/workflows/service.py` | Tree validation and revision-checked persistence. |
| `backend/app/workflows/numbering.py` | Stable per-agent step numbers assigned during a locked save. |
| `backend/app/workflows/matching` | Fuzzy scoring, embedding adapters/cache, Groq recognition, and layered trigger selection. |
| `backend/app/workflows/runtime.py` | Trigger matching, sequential actions, nested waits, and continuations. |

Live agent handoff, External handoff, AI agent handoff, Create ticket, Auto fill field, Auto team match, and Send graph are reserved for subsequent configuration work. Send API makes actual configured HTTPS requests, SQL Query executes read-only PostgreSQL lookups, and Switch Language changes the current chat language. External handoff, ticket email, and channel delivery remain subsequent integrations.

## SQL Query and Settings (0.8.0)

SQL Query executes a single parameterized SELECT against a verified PostgreSQL connector in the current workspace, in a server-enforced read-only transaction. It stores `{ok,rows,columns,row_count,truncated,error}` and selects Success, Error, or Timeout. Empty rows still mean Success. All workflow traversals and Jump targets include SQL outcome paths. See [SQL_QUERY.md](SQL_QUERY.md) for the complete survey → SQL → Branch → message → menu flow and [DATABASE_CONNECTIONS.md](DATABASE_CONNECTIONS.md) for Settings setup, permissions, encrypted credentials, TLS, and migration `0006`. Other new Settings sections are explicitly marked Planned; existing role controls remain functional.

## Switch Language (0.9.0)

Choose an agent-configured language in **Flow control → Switch Language**. It changes the active conversation language without creating an assistant message or changing the agent default. The language persists through translated messages, triggers on subsequent turns, surveys, Branch, Jump, API/SQL parameters, and generative fallback prompts. Existing state/CSRF/permissions/revision checks apply. Switch Language itself introduced no migration or dependency. The current Send Attachment release requires schema head `0007`. See [SWITCH_LANGUAGE.md](SWITCH_LANGUAGE.md) for setup, missing-translation behavior, removal/restore guards, API config, and an English/French menu test.


## Send Attachment (0.10.0)

Choose **Messaging → Send attachment**, select the content language and file format, and provide an upload (up to 20 MiB), HTTP(S) file link, or safe Python expression returning a URL. The tester displays images, audio/video controls, and downloadable document cards. The action emits a single assistant file message, then continues normally; it works with nested triggers, Branch, Jump, and API/SQL outcome paths. Uploaded files are agent-owned and validated on save; sent copies belong to the conversation and survive later asset replacement. Localisation uses the active language and the existing default/first fallback, without translating file contents. Apply migration **0007** before running the updated backend. See [SEND_ATTACHMENT.md](SEND_ATTACHMENT.md) for supported extensions, script examples, limits, access, and a complete complaint form flow.


## Show Carousel (0.11.0)

Choose **Messaging → Show carousel** and configure 1–10 cards in each content language, with optional uploaded or hosted images, titles/subtitles, and up to three buttons per card. Reply buttons send their configured message through normal nested/root Message received matching. Link buttons open a new tab or a sandboxed webview inside the tester. Cards support drag and up/down reordering, horizontal navigation, and the existing active/default/first language fallback. Uploaded-image references participate in the same ownership, deletion protection, and immutable sent-copy behavior as Send Attachment. No migration or dependency is added; head remains `0007`. Read [SHOW_CAROUSEL.md](SHOW_CAROUSEL.md) for a complete complaint-category flow and webview limitations.

## Chat Closed and Collect Feedback (0.13.0)

Open the permanent **Chat closed** workflow to configure post-conversation actions. Its catalogue contains the 13 allowed action types, with four future types marked Later. Send message, Send attachment, Show carousel, Ask survey, and incoming triggers are unavailable, including in Branch/API/SQL/LLM paths or indirect Jump continuations. The backend enforces the same policy. Normal workflows cannot Jump into its steps.

Choose **Flow control → Collect feedback** and configure its **Rating**, **Survey**, and **Settings** tabs. Rating saves an integer in `feedback_rating` or your chosen variable; optional/required Open response, Number, Email, Phone, and Multiple choice fields save additional variables. Titles, request messages, questions, placeholders, choices, and submission confirmations are localisable. The separate feedback form pauses the action’s children and remaining steps until submission, then saves all answers before resuming. Put mandatory closure logs before it; put actions needing feedback after it. This replaces 0.12.0’s immediate continuation behavior.

This `kind:"action"` requests feedback; the optional separate `kind:"trigger"` with the same type runs after submission and continuation. Closure and accepted answers are committed before their automation, so an automation error cannot reopen the chat or lose the answers. Test using **Chat closed (live-agent event)** and the channel preview selector; actual Livechats/published-channel integration comes later. See [CHAT_CLOSED.md](CHAT_CLOSED.md) for the combined flow and [COLLECT_FEEDBACK.md](COLLECT_FEEDBACK.md) for all settings and survey-only testing. No new migration or dependency is required; head remains `0007`.

## Close Chat (0.14.0)

Choose **Add action → Flow control → Close chat**, marked Ready. It has no extra settings. Save it after a goodbye message or other actions that must happen before closing. For an open conversation it stops its children, later siblings, and enclosing continuations, then invokes the permanent Chat Closed workflow once. If Collect Feedback is configured there, the closed tester displays its form and resumes the post-close continuation after submission. When already closed, Close chat is a no-op that continues without firing the trigger again. Disabled actions are skipped normally.

Closure, earlier messages/media, and collected variables are committed before the close automation; a later failure leaves the chat closed and those earlier results intact. Trace entries show Closed or Already closed, and the response records `close_source:"workflow"`. No migration or dependency is added. See [CLOSE_CHAT.md](CLOSE_CHAT.md) for the complete end-to-feedback test.


## LLM Processing (0.15.0)

Choose **Add action → AI & data → LLM Processing**, marked Ready. The editor provides Input and System prompt with optional safe Python Script modes, the configured Groq model, temperature 0–2 (default 1), a Knowledge toggle, and a resulting variable. Advanced settings select a 1–120 second timeout (default 30). The generated final answer is stored directly as text; reference `{complaint_details}` in a subsequent Send message.

Save the action, then add actions under Success, Error, and Timeout. Paths participate in step numbering, editing, insertion, reordering, Branch, Jump target search, nested waits, and post-close restrictions. Model errors clear stale result text. The tester shows the model and short error code in its trace. The action itself emits no chat message, so it can process variables under Chat Closed and after Collect Feedback submission.

The Knowledge tab now saves per-agent enabled text sources; uploads, crawling, and retrieval ranking remain future features. Read [LLM_PROCESSING.md](LLM_PROCESSING.md) and [KNOWLEDGE.md](KNOWLEDGE.md). Apply migration **0008** before starting this release. Current Chat Closed catalogue has four future entries marked Later; LLM Processing is Ready.
