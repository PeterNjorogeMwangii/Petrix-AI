# Petrix AI API

API paths begin with `/api`. Use the same public origin for the frontend and `/api` reverse proxy. FastAPI exposes OpenAPI documentation at `/docs` when running locally.

## Authentication

| Method | Path | Request | Result |
| --- | --- | --- | --- |
| GET | `/api/auth/options` | None | Availability of email, Google, and Microsoft; configured magic-link lifetime |
| POST | `/api/auth/magic-link` | JSON `{ "email": "you@gmail.com" }` | 202 after SMTP accepts the message; generic eligibility message |
| POST | `/api/auth/verify` | JSON `{ "token": "opaque-token-from-email" }` | Authenticated account/workspace context; session and CSRF cookies |
| GET | `/api/auth/me` | Session cookie | Current account/workspace context and next onboarding/dashboard path |
| POST | `/api/auth/logout` | Session cookie and CSRF header | 204; revoke current session and remove cookies |
| GET | `/api/auth/oauth/{provider}/start` | `provider` is `google` or `microsoft` | Redirect to configured provider |
| GET | `/api/auth/oauth/{provider}/callback` | Provider authorization response | Validate authentication and redirect to setup, dashboard, or email association |
| POST | `/api/auth/provider-email/info` | JSON `{ "token": "opaque-provider-ticket" }` | Provider name and optional email hint |
| POST | `/api/auth/provider-email` | JSON `{ "ticket": "opaque-provider-ticket", "email": "you@example.com" }` | Request mailbox verification for first provider association |

The magic-link email contains `/auth/verify#token=...`. The fragment keeps the token out of ordinary server URL logs. The frontend reads and removes the fragment, and submits the token only after the user selects Continue. A GET request never consumes a magic link.

Account context example:

```json
{
  "user": { "id": "user-uuid", "email": "you@gmail.com", "full_name": null },
  "workspace": {
    "id": "workspace-uuid",
    "name": "My AI Workspace",
    "role": "owner",
    "industry": null,
    "country_code": null,
    "website": null,
    "setup_completed": false,
    "avatar_url": null,
    "permissions": { "manage_ai_agents": true, "manage_workspace_roles": true },
    "subscription": { "plan_name": "Starter", "agent_limit": 3, "language_limit": 3 }
  },
  "next_path": "/onboarding/workspace"
}
```

## Workspaces

| Method | Path | Request | Result |
| --- | --- | --- | --- |
| GET | `/api/onboarding/options` | Session cookie | Industries, supported countries, optional default country |
| GET | `/api/workspaces/{id}` | Session cookie | Metadata only for a workspace the user belongs to |
| POST | `/api/workspaces/{id}/setup` | Multipart form, session cookie, CSRF header | Save fields; mark setup complete; return dashboard path |
| GET | `/api/workspaces/{id}/avatar` | Session cookie | Sanitized PNG avatar for a workspace member |
| GET | `/api/health` | None | 200 when the database is reachable; 503 otherwise |

Workspace setup form fields:

| Field | Rule |
| --- | --- |
| `name` | Required; trimmed; 1–100 characters |
| `country_code` | Required; valid supported ISO 3166-1 alpha-2 code |
| `industry` | One of the offered industries; defaults to Other |
| `website` | Optional; full HTTP(S) URL |
| `avatar` | Optional file; actual PNG, JPEG, or WebP; at most 2 MiB and 16 million source pixels |

The avatar is re-encoded to a 256×256 PNG, with metadata removed, and stored in PostgreSQL. Repeat setup requests update the same workspace. Membership and Owner permission are checked by the backend.

## Session and error contract

The `petrix_session` cookie is HttpOnly, SameSite=Lax, and Secure in production. Its token is hashed in the database. The readable `petrix_csrf` cookie must be echoed in `X-Petrix-CSRF` for authenticated mutations. Angular's HTTP client handles that automatically for relative same-origin API requests.

Errors use FastAPI's `detail` response field. The UI shows failure messages after failed requests and advances only after successful API responses.

| Status | Meaning |
| --- | --- |
| 400 | Expired/used authentication request, invalid provider association, or blocked email |
| 401 | Missing, expired, or revoked session |
| 403 | Failed CSRF check, disallowed request origin, or insufficient role |
| 404 | Workspace unavailable to this user, or no avatar |
| 409 | Identity already belongs to another account, or inconsistent membership |
| 422 | Invalid email, workspace fields, country, industry, or avatar |
| 429 | Authentication request limit reached; `Retry-After` included |
| 503 | Email delivery or database temporarily unavailable |

## AI agents

The prefix below is `/api/workspaces/{workspace_id}/agents`. All requests require a valid session and membership in that workspace. Mutations require the CSRF header and Manage AI Agents permission. The workspace must have completed onboarding. All resource lookups are scoped to the workspace.

| Method | Path after prefix | Request | Result |
| --- | --- | --- | --- |
| GET | Empty | None | Agents, current permission, plan capacity and usage |
| POST | Empty | Multipart `name`, `default_language`, optional `avatar` | 201; saved agent, version 1 |
| POST | `/import` | Multipart `file` containing a Petrix JSON export | 201; new agent, version 1 |
| GET | `/{agent_id}` | None | Saved configuration and revision |
| GET | `/{agent_id}/avatar` | None | Sanitized PNG |
| GET | `/{agent_id}/export` | Manage permission | Portable JSON download |
| POST | `/{agent_id}/personalization` | JSON `expected_revision`, `llm_enabled`, `persona` | Saved agent with updated revision |
| POST | `/{agent_id}/languages` | JSON `expected_revision`, `languages`, `default_language` | Saved agent; selected-language/quota checks |
| POST | `/{agent_id}/identity` | Multipart `expected_revision`, `name`, optional `avatar`, `remove_avatar` | Saved agent |
| POST | `/{agent_id}/voice` | JSON `expected_revision`, `voice_enabled`, `voice_name`, `voice_speed` | Saved voice preferences |
| POST | `/{agent_id}/delete` | JSON `expected_revision`, exact `confirmation_name` | 204; configuration deleted, historical chats retained |
| GET | `/{agent_id}/versions?offset=0` | None | Version metadata, `has_more` |
| GET | `/{agent_id}/versions/{number}` | None | Snapshot metadata and configuration |
| POST | `/{agent_id}/versions/{number}/restore` | JSON `expected_revision` | Saved agent; earlier history retained |
| GET | `/{agent_id}/changes?offset=0` | None | Change history, `has_more` |
| GET | `/{agent_id}/network` | None | Workspace nodes and directed connections |
| POST | `/{agent_id}/network` | JSON `target_id` | 201; same-workspace connection |
| POST | `/{agent_id}/network/{connection_id}/delete` | Empty JSON | 204; remove an outgoing connection |
| POST | `/{agent_id}/tester/messages` | JSON `content`, optional `conversation_id` | Model reply and conversation ID |

`GET /api/agents/options` returns the language catalog, upload size, import contract, and configured tester/voice-runtime availability.

`GET /api/workspaces/{workspace_id}/roles` and `POST` on the same path require Owner or Administrator role. The POST JSON contains Boolean `admin` and `member` fields for Manage AI Agents. The Owner grant is immutable.

Version and change-log pages contain up to 50 records. Stale revisions and capacity conflicts return 409; invalid fields/images/languages/imports return 422; an import larger than 15 MiB returns 413. Unconfigured or failing model connections return 503. Tester request limits return 429.

See [AI_AGENTS.md](AI_AGENTS.md) for configuration, import examples, retention policy, and provider setup.

## Workflows and event tester

Milestone 3 adds these paths beneath `/api/workspaces/{workspace_id}/agents/{agent_id}`:

| Method | Path | Request | Result |
| --- | --- | --- | --- |
| GET | `/workflows` | Session and membership | Saved tree, workflow revision, trigger/action catalogue |
| POST | `/workflows` | Manage permission, CSRF; JSON `expected_revision`, `nodes` | Validated tree and new workflow revision |
| POST | `/tester/events` | Manage permission, CSRF; JSON `event`, event fields, optional language, conversation ID after start | Saved conversation ID, assistant messages, quick replies, execution trace, lifecycle state |

Each saved workflow node includes a positive integer `number`, unique within the agent across triggers and actions. The backend assigns it on creation and preserves it by node UUID on subsequent saves, including reordering. Deleted numbers are not reused. Clients may omit `number`; supplied values cannot override server assignments. Use the returned tree after saving to obtain new numbers. The tester's trace includes `node_number` alongside `node_id`; both are null for a generative reply. The allocation counter is internal and is not a client-editable document field.

Version `0.4.0` adds `matching` to tester responses: method, scope, selected node ID/number/name, score, entities, optional context summary, clarification fields, stage attempts, and controlled provider warnings. `match_mode: "approximate"` now uses fuzzy → semantic → Groq recognition when configured; `"exact"` opts out of the added stages. A clarification response has message source `clarification`, executes no workflow actions, and preserves the relevant waiting context. Entities are available to actions through `system["intent"]`; they are not automatically copied into custom variables. See [INTENT_MATCHING.md](INTENT_MATCHING.md).

Start with `{"event":"chat_started"}`. Subsequent events require that response's `conversation_id`. Message events use `{"event":"message_received","conversation_id":"...","content":"Balance"}`. Attachment events use `attachment: {name, media_type}` with `media_type` one of `image`, `audio`, `video`, or `file`; these are simulated metadata. As of 0.12.0, `collect_feedback` requires a closed chat, a pending rating request, its `feedback_token`, and an integer `rating` from 1 to 5. Other supported events are `chat_closed`, `chat_timeout`, and `ai_timeout`.

The Angular tester uses `/tester/events`, including its saved greeting. `/tester/messages` remains available as the earlier direct generative endpoint for compatibility; it does not execute workflows. See [WORKFLOWS.md](WORKFLOWS.md) for node schemas, ordering rules, matching, supported placeholders, and current limits.

Set variable is now available as `type: "set_variable"`, with config fields `variable_name` and `chat_script`. It stores a typed value in the current conversation and creates no assistant message. `/tester/events` includes `variables` in its response. Variable assignment errors and missing message placeholders return 422, rolling back that turn's data and messages. Existing session/CSRF/role/ownership checks and revision-based workflow saves apply. See [SET_VARIABLE.md](SET_VARIABLE.md) for expressions, system variables, limits, and the complete example.


Branch is available as `type: "branch"`. Its config contains 1–10 ordered `{id, name, chat_script}` conditions and `else_path_id`. Store its distinct action lists in `paths: [{id, children}]`, with exactly one matching path per condition and Else; ordinary `children` must be empty. Path IDs are unique UUIDs and have no step number. Node validation, numbering, and Jump reference checks traverse every path. Tester traces include `outcome: "branched"`, `path_id`, and `condition_name`. The first truthy condition wins; errors return 422 with transactional rollback. See [BRANCH.md](BRANCH.md) for examples, survey-value syntax, and continuation behavior.


Send API is available as `type: "send_api"`. Config fields are `method`, `url`, `headers: [{key,value}]`, `payload_mode` (`json` or `expression`), `payload`, `variable_name`, `timeout_seconds` (default 30), and three UUIDs: `success_path_id`, `error_path_id`, `timeout_path_id`. Store one matching `paths: [{id,children}]` container per outcome and keep `children` empty. The result variable contains `{ok,status_code,body,error}`. Traces include the selected path, outcome, HTTP status, and controlled error code. Authentication and workflow revision checks are unchanged. See [SEND_API.md](SEND_API.md) for request/response formats, limits, private-network configuration, and handling external side effects.


## Database connections and SQL Query (0.8.0)

Database routes use `/api/workspaces/{workspace_id}/database-connections`. Normal cookie sessions, workspace membership, CSRF on writes, and origin checks apply. Owners/admins manage connections; Manage AI Agents allows access to safe verified connector options.

| Method | Suffix | Purpose |
| --- | --- | --- |
| GET | empty | Connection table and supported types; never returns passwords or ciphertext |
| GET | `/options` | Verified `{id,name,type}` choices in this workspace for workflow configuration |
| POST | empty | Create, encrypt password, and verify connection; 201 with verified/error status |
| POST | `/{id}` | Update fields with `expected_revision`, optional password, and reverify |
| POST | `/{id}/verify` | Recheck access; requires `expected_revision` |
| POST | `/{id}/delete` | Delete an unused connection; requires `expected_revision` |

Fields: `name`, `type: "postgresql"`, `host`, `port` (default 5432), `database`, `username`, `password`, `ssl_mode` (`verify-full`, `require`, `disable`; default verify-full). An omitted/null/empty password on edit retains the saved secret. Names are case-insensitively unique. Responses include status, verification time, a controlled error code, revision, and timestamps. Failed verification is persisted as `status: "error"` and excluded from options. Stale changes and deletion of referenced connections return 409. Foreign IDs return 404. Up to 20 connectors per workspace.

SQL Query has `type: "sql_query"` and config `connection_id`, `query`, `query_mode` (`template` or `expression`), `timeout_seconds` (1–120, default 30), `variable_name`, and three distinct UUIDs: `success_path_id`, `error_path_id`, `timeout_path_id`. Keep normal `children` empty; each ID must have one matching `paths: [{id,children}]` container. Workflow save validates connector ownership/verification. Runtime rechecks the connector, binds scalar chat parameters, applies read-only transactions and timeouts, and returns exactly one outcome. The result is `{ok,rows,columns,row_count,truncated,error}`. Traces include `outcome`, `path_id`, `row_count`, and a controlled `error`. An empty result follows Success. See [SQL_QUERY.md](SQL_QUERY.md) for limits and data formats.


## Switch Language (0.9.0)

`type: "switch_language"` is now available, with `config: {"target_language":"fr"}`. Choose a supported language code already present in this agent's configured languages. A workflow save rejects unavailable targets at every nesting level; runtime checks them again. The action uses ordinary children, emits no assistant message, and updates the conversation's existing `language` state. Tester responses use the existing top-level `language`; its trace entry also contains `previous_language` and `target_language`. Branch/scripts see the new `language` immediately. The workflow tester passes the active language to generative fallback prompts, while the legacy `/tester/messages` endpoint retains its existing default behavior.

Language settings changes and agent-version restoration reject removal of a language referenced by any saved Switch Language action (including disabled ones). Update or remove those actions first. Existing workspace/agent ownership, Manage AI Agents, CSRF, workflow revisions, and conversation checks are unchanged. Switch Language itself introduced no migration; the current Send Attachment release requires head 0007. See [SWITCH_LANGUAGE.md](SWITCH_LANGUAGE.md).


## Send Attachment (0.10.0)

All paths below use `/api/workspaces/{workspace_id}/agents/{agent_id}` and the current session. Mutations also require Manage AI Agents permission and the `X-Petrix-CSRF` header.

| Method and path | Request / result |
| --- | --- |
| `POST /workflow-assets` | Multipart `file_format` (`image`, `audio`, `video`, `document`) and one `file`, at most 20 MiB. Returns 201 with `{id,name,format,content_type,size_bytes,url}`. |
| `GET /workflow-assets/{id}` | Agent/workspace-scoped upload metadata. |
| `GET /workflow-assets/{id}/file` | Authenticated download; `?inline=true` permits supported image/audio/video MIME previews. Byte ranges return 206; invalid ranges return 416. Documents remain downloads. |
| `POST /workflow-assets/{id}/delete` | Deletes an unused upload. Returns 409 while any saved action translation references it, including disabled/nested/outcome steps. |
| `GET /tester/attachments/{id}` | Conversation-owner download. Also supports safe inline media and byte ranges. |

An action uses this workflow config:

```json
{"translations":{"en":{"file_format":"document","source":"upload","asset_id":"<uploaded-asset-uuid>","url":"","chat_script":""}}}
```

For `source:"link"`, set `url` to an HTTP(S) file URL and `asset_id` to null. For `source:"script"`, set `chat_script` to a safe Python expression returning a URL string. Inactive source fields are cleared when saved. Asset references must belong to this agent/workspace and match the selected format. Per-language settings follow Send message's active/default/first fallback. Runtime script URLs may also be an attachment-survey URL belonging to the current conversation.

The existing revision-checked `POST /workflows` saves the action. Tester output contains `source:"send_attachment"`, the action's `node_id`, empty `content`/`quick_replies`, and a one-item `attachments` array. Each file has `{id,name,url,content_type,size_bytes,format,external}`. Hosted files have `size_bytes:null` and `external:true`; no backend request is made. Uploaded assets are copied into conversation-owned storage and return a conversation download URL. Ten attachment actions may run per turn; a failed action rolls back that turn, including newly delivered file copies. Each agent has a 100-file/1-GiB asset quota. See [SEND_ATTACHMENT.md](SEND_ATTACHMENT.md). Schema head is **0007**.


## Show Carousel (0.11.0)

The existing revision-checked `POST /api/workspaces/{workspace_id}/agents/{agent_id}/workflows` accepts `type:"show_carousel"` with this config shape:

```json
{
  "translations": {
    "en": {
      "cards": [{
        "id": "bd45348c-4b75-4942-93ca-8b35bf4eb54f",
        "title": "Transactions",
        "subtitle": "Get help with a transaction.",
        "image": null,
        "buttons": [{
          "id": "1665cc85-5481-470b-a3bc-4943642288ed",
          "label": "Select",
          "type": "reply",
          "message": "Transactions",
          "url": "",
          "webview": false
        }]
      }]
    }
  }
}
```

Each translation requires 1–10 cards and at most three buttons per card. Cards and buttons have UUID identities unique within that language. Text limits are title/label 80, subtitle 200, reply message 1,000; URLs use at most 2,048 characters. Image is null, `{source:"upload",asset_id:"<image-asset-uuid>",url:""}`, or `{source:"link",asset_id:null,url:"https://example.com/image.png"}`. Upload through the existing multipart `/workflow-assets` endpoint with `file_format=image`. Foreign, missing, and non-image upload references are rejected, including in disabled or nested cards.

A `type:"link"` button requires a complete HTTP(S) `url`; `webview:true` requests the tester's embedded view. Page anchors and query strings are accepted. Inactive message/URL/webview fields are cleared as appropriate on save. Text fields support existing variable rendering; URL fields are fixed URLs. Existing CSRF, role, workspace, workflow revision, and conversation ownership requirements apply.

Tester output contains one assistant message with empty `content`, empty `quick_replies`, `source:"show_carousel"`, its `node_id`, and `carousel:{cards:[...]}`. Runtime cards preserve IDs and ordering, contain rendered text/button values, and replace image configuration with ordinary image metadata or null. Uploaded images use conversation-owner download URLs and immutable snapshots; hosted images use their original URL with `external:true` and `size_bytes:null`. Image snapshots are reused within a turn when the same upload occurs in multiple cards. Up to ten carousels can execute in one turn.

Reply selection posts the configured text through the existing `message_received` event, so nested triggers can continue to survey, Branch, API/SQL, Jump or other actions. Link/webview clicks do not post tester events. The backend does not fetch external images or webpages. See [SHOW_CAROUSEL.md](SHOW_CAROUSEL.md) for editor behavior, localization, reference protection, webview constraints and a complete example. No new endpoint, migration, or dependency is required.

## Chat Closed and Collect Feedback (0.12.0)

`GET /workflows` includes two flags on each action definition: `allowed_after_close` identifies the 13 post-conversation types; `post_close_only:true` identifies Collect Feedback. Planned action definitions remain `available:false`. Chat Closed's entire subtree must contain allowed actions, including disabled children and outcome paths. Jump destinations and their automatic continuations are validated across roots; incoming waits and conversational actions are rejected with 422. Incoming triggers cannot be nested under Chat Closed. Ordinary workflows cannot Jump into its steps.

Save the new action with `kind:"action", type:"collect_feedback"` and config:

```json
{
  "rating_enabled": true,
  "translations": {
    "en": {"webchat_rating_title": "Please rate the conversation"},
    "fr": {"webchat_rating_title": "Veuillez évaluer la conversation"}
  }
}
```

The flag must be a JSON boolean. While enabled, at least one translation is required and each configured title must be nonempty and at most 500 characters. Titles support existing text substitutions. The action must be physically beneath the permanent Chat Closed trigger. Existing feedback receiving triggers retain `kind:"trigger", type:"collect_feedback"` with their existing config.

Post `{"event":"chat_closed","conversation_id":"..."}` to `/tester/events` to simulate a live-agent close event. The conversation is marked closed before automation, pending surveys/waits are cancelled, and later normal events are rejected with 409. Tester closures record actor/time and `close_source:"tester"`. Chat timeout does not invoke this trigger. Livechats/Inbox and published-channel close events are not integrated by this release.

Tester responses now include `closed_at`, `csat_rating`, `automation_error`, and `feedback`. With an outstanding request, `feedback` has this shape (IDs below are illustrative):

```json
{
  "token": "eab1be7d-513b-4f2a-8f56-a05fcd1b2b39",
  "title": "Please rate the conversation",
  "node_id": "e6e37774-841c-412c-bb88-a7f91973ff96",
  "node_number": 7,
  "language": "en",
  "min_rating": 1,
  "max_rating": 5
}
```

It is structured state, not an assistant message. The first enabled request issued during closure is retained. The action traces `outcome:"rating_requested"` and then continues. The title is resolved when issued and remains valid even after saved workflow changes.

Submit `{"event":"collect_feedback","conversation_id":"...","rating":4,"feedback_token":"..."}`. A strict integer 1–5 is required; booleans, numeric strings, fractional values, and missing/out-of-range ratings return 422. A missing/wrong/foreign request token, no outstanding request, an open chat, or a second submission returns 409. Ownership/permission/CSRF checks still apply. On success `feedback:null`, `feedback_submitted:true`, and `csat_rating:4` are returned. An optional matching feedback trigger may emit its own messages; without one, rating storage completes without generic fallback or an artificial assistant reply.

Closing and rating acceptance are committed before their workflow automation runs. Expected workflow failures return **200 with the actual closed/accepted state**, a nonempty `automation_error`, diagnostic `notice`, and `outcome:"automation_failed"` trace. Failed automation state changes are rolled back; closure or the accepted rating remains stored. Retrying the close event or accepted rating returns 409 and does not replay external effects. Successful ordinary turns retain their existing atomic behavior. There is no automatic lifecycle retry or distributed rollback of external API calls.

The frontend disables normal chat controls after closure and renders the issued rating panel separately. Both `/tester/events` and the older `/tester/messages` endpoint reject normal replies to a closed conversation; the latter makes no model call for that conversation. Client integrations must use the returned feedback token rather than posting arbitrary CSAT. See [CHAT_CLOSED.md](CHAT_CLOSED.md). No new dependency or migration; schema head remains **0007**.

## Complete Collect Feedback (0.13.0)

This extends the 0.12.0 lifecycle contract above. Chat Closed remains permanent and restricted to its 13 allowed types. Collect Feedback supports ratings, survey answers, or both; it now pauses the remaining post-close actions until submission. Existing 0.12.0 configs and outstanding rating tokens remain readable without a migration.

Save `kind:"action", type:"collect_feedback"` beneath Chat Closed with this configuration (field UUID is illustrative):

```json
{
  "rating_enabled": true,
  "rating_variable_name": "feedback_rating",
  "survey_enabled": true,
  "fields": [
    {"id":"960979b8-3832-4495-b482-2282a20d0b39","reply_type":"open_response","required":false,"variable_name":"feedback_comment"}
  ],
  "translations": {
    "en": {
      "webchat_rating_title": "How did we do?",
      "rating_request_message": "How would you rate your conversation?",
      "submission_text": "Thank you for your feedback!",
      "fields": {
        "960979b8-3832-4495-b482-2282a20d0b39": {"question":"Any other comments?","placeholder":"Please share your comments","choices":[]}
      }
    }
  }
}
```

`rating_enabled`, `survey_enabled`, and each `required` are strict booleans. Up to 50 unique fields are supported. `reply_type` is `open_response`, `number`, `email`, `phone`, or `choice`. Choice translations require 1–13 distinct nonempty strings of at most 100 characters. Questions are 1–1,000 characters, placeholders at most 200, rating titles at most 500, and rating requests/submission text at most 4,000. Unknown language/field IDs, duplicate variable names, reserved names, and incomplete enabled settings return 422. Variable names follow existing 1–64-character naming and capacity rules; `feedback_rating` is now an ordinary persisted variable, and `language` cannot be used for feedback storage.

Each translation contains its localised text and optional field translations keyed by the shared field UUID. Every enabled survey field needs a question in at least one language. Missing field translations use active/default/first fallback independently; all top-level configured translations require submission text and, when rating is enabled, both rating prompts. Legacy rating-only configs gain default request/confirmation text when validated or saved.

Start the tester with optional `channel:"webchat"|"messaging"|"bitrix24"|"africas_talking"` (default `webchat`). The channel is stored on the conversation; a conflicting later channel returns 409 and requires a new conversation. This is a tester preview contract, not a transport connection.

After `chat_closed`, response `feedback` retains `token`, `title`, `node_id`, `node_number`, `language`, `min_rating`, and `max_rating`, and adds:

| Field | Meaning |
| --- | --- |
| `rating_enabled` | Whether a rating is requested |
| `rating_variable_name` | Stored rating variable |
| `request_message` | Resolved non-Webchat rating prompt |
| `channel` | Stored tester preview channel |
| `typed_rating` | True for Bitrix24/Africa’s Talking |
| `rating_buttons` | `[1,2,3,4,5]` for Messaging buttons; otherwise `[]` |
| `fields` | Ordered issued fields with shared `id`, `reply_type`, `required`, `variable_name`, resolved `question`, `placeholder`, and translated `choices` |

Private workflow revision/continuation and submission template are not returned in the public form. `waiting` continues to describe ordinary chat waits and stays false for the closed feedback form. `close_automation.status` is stored as `waiting_feedback` while the form is outstanding.

Submit all enabled parts through the existing `/tester/events` route:

```json
{
  "event": "collect_feedback",
  "conversation_id": "<conversation UUID>",
  "feedback_token": "<issued token UUID>",
  "rating": 4,
  "feedback_answers": {
    "960979b8-3832-4495-b482-2282a20d0b39": "Very helpful agent."
  }
}
```

`feedback_answers` is a UUID-keyed map of strings, up to 50 entries and 4,000 characters each. Number/Email/Phone strings are validated and normalized into typed stored values. Only issued fields are accepted. Required blanks, invalid formats, malformed/foreign field IDs, extra answers, oversized text, booleans/nested answer values, missing enabled ratings, or a rating supplied for a survey-only form return 422 **without consuming the token or partially saving variables**. Optional blanks save `null`. Rating remains a strict integer 1–5 when enabled. For survey-only forms, omit `rating`. Token, one-submission, permission, ownership, CSRF, and rate-limit checks remain enforced.

Acceptance commits the answer variables, optional `csat_rating`, submission flag/time, and removes the pending form before resuming actions. Normal success returns `feedback:null`, `feedback_submitted:true`, `feedback_confirmation` (the resolved localised Settings text), optional rating, and the variables. Confirmation placeholders resolve with submitted variables. The action’s children and remaining post-close actions resume once, then the optional existing feedback receiving trigger runs. Without that trigger no generic fallback runs. Both features off means no form is issued and actions continue immediately.

An action after Collect Feedback can now use `feedback_rating` and `feedback_comment` directly. Use actions before it for closure work that must run without a submission. A workflow revision change preserves the original form’s answer/variable snapshot but blocks its stale continuation after saving the answers. Legacy 0.12.0 outstanding requests have no saved continuation and are accepted without replaying previously completed close actions.

Expected continuation/confirmation/callback errors return 200 with accepted feedback, stored variables, and `automation_error`; accepted answers remain saved and duplicates return 409. Partial automation changes are rolled back. External effects are not automatically retried or undone. The chat remains closed throughout; normal messages and generic chat waits stay unavailable. See [CHAT_CLOSED.md](CHAT_CLOSED.md) and [COLLECT_FEEDBACK.md](COLLECT_FEEDBACK.md). Schema head remains **0007**; no new dependency.

## Close Chat (0.14.0)

The existing action catalogue reports `type:"close_chat", available:true, allowed_after_close:true, post_close_only:false`. Save the action with `kind:"action"`, `type:"close_chat"`, `config:{}`, and normal name/description/enabled/children fields. Unknown configuration fields are rejected with 422. Authentication, Manage AI Agents, CSRF, ownership, and optimistic workflow revision checks are unchanged.

When the action executes in an open conversation, the interpreter returns a server-owned close command and stops its current flow. The route persists the incoming turn, earlier output messages/files, collected variables, close time/actor, `close_source:"workflow"`, and the closing action ID/number before running the permanent `chat_closed` trigger once. No second client event is needed. Actions following the open-flow Close chat, its children, and enclosing continuations do not execute.

The existing `/tester/events` response includes `closed:true`, `closed_at`, `close_source`, earlier messages and a combined trace. Close action traces use `type:"close_chat", outcome:"closed"`; a Close chat encountered in an already-closed conversation traces `outcome:"already_closed"`, continues normally, and does not dispatch another closure. Existing feedback request/token/submission behavior is unchanged. The explicit tester close event continues to record `close_source:"tester"`.

A failure before reaching Close chat rolls back the ordinary turn and leaves the conversation open. A failure in the following Chat Closed automation returns 200 with `closed:true`, the earlier committed messages/files/variables, a visible `automation_error`, and a close-automation failure trace. Partial post-close state changes roll back. Repeated ordinary messages or close events return 409 and do not replay the workflow. External side effects are not automatically retried or undone.

This action is usable in the current tester, including Branch paths, API/SQL outcomes, survey completion, and Jump destinations. Livechats controls and published-channel delivery remain separate milestones. No new migration/dependency is added; schema head remains **0007**. See [CLOSE_CHAT.md](CLOSE_CHAT.md).


## LLM Processing and agent Knowledge (0.15.0)

`GET /api/workspaces/{workspace_id}/agents/{agent_id}/workflows` adds `llm_processing` metadata containing `model`, `provider:"Groq"`, `available`, and `knowledge_supported`. Availability reflects the server key; the catalogue action remains Ready so it can be configured before the key is installed. Save workflow nodes with `kind:"action"`, `type:"llm_processing"`, and this config (use real UUIDs for the three path IDs):

```json
{
  "input": "{description.text}",
  "input_mode": "template",
  "model": "intent",
  "temperature": 0.2,
  "system_prompt": "Return a concise, factual complaint summary.",
  "system_prompt_mode": "template",
  "use_knowledge_info": false,
  "variable_name": "complaint_details",
  "timeout_seconds": 30,
  "success_path_id": "9fa97348-39a6-4fd7-b998-552f283ec6b7",
  "error_path_id": "8a9da50d-32b3-4f7f-a58f-cdf103c3946b",
  "timeout_path_id": "bf0f9b90-0ba0-4262-838e-5cdbe78c9602"
}
```

The node needs `children:[]` and exactly three `paths` entries with the matching IDs and their own `children`. Both prompt modes accept `template` or `expression`. Expressions use the existing safe Python interpreter. Temperature must be numeric, finite, and 0–2. Timeout must be an integer 1–120. Model is the server-controlled `intent` alias, not an arbitrary provider or API key.

Success stores a string directly in the named variable. Error/Timeout stores null instead of retaining stale output, provided a variable slot is available. Trace entries add `model`, `knowledge_sources`, `path_id`, and sanitized `error` to the normal node/outcome fields. No message is emitted by the action. See [LLM_PROCESSING.md](LLM_PROCESSING.md) for all limits and error codes.

| Method | Path | Request/result |
| --- | --- | --- |
| GET | `/api/workspaces/{workspace_id}/agents/{agent_id}/knowledge` | Returns `agent_id`, `revision`, `sources`, and `updated_at`; no row is created by GET. |
| POST | Same path | `{ "expected_revision": 1, "sources": [{ "id": "source-uuid", "name": "Policy", "content": "Policy text", "enabled": true }] }`; returns the saved document. |

Knowledge mutations require Manage AI Agents and CSRF; reads require workspace membership. Foreign workspace/agent combinations return 404. A stale revision returns 409. Source IDs must be unique, titles/content nonempty, enabled strictly boolean, at most 20 sources, and at most 64 KiB of combined UTF-8 title/body content. Changed saves increment revision and audit without source content. Sources are independent of agent settings exports/restore. See [KNOWLEDGE.md](KNOWLEDGE.md).
