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

Start with `{"event":"chat_started"}`. Subsequent events require that response's `conversation_id`. Message events use `{"event":"message_received","conversation_id":"...","content":"Balance"}`. Attachment events use `attachment: {name, media_type}` with `media_type` one of `image`, `audio`, `video`, or `file`; these are simulated metadata. `collect_feedback` requires a closed chat and a `rating` from 1 to 5. Other supported events are `chat_closed`, `chat_timeout`, and `ai_timeout`.

The Angular tester uses `/tester/events`, including its saved greeting. `/tester/messages` remains available as the earlier direct generative endpoint for compatibility; it does not execute workflows. See [WORKFLOWS.md](WORKFLOWS.md) for node schemas, ordering rules, matching, supported placeholders, and current limits.

Set variable is now available as `type: "set_variable"`, with config fields `variable_name` and `chat_script`. It stores a typed value in the current conversation and creates no assistant message. `/tester/events` includes `variables` in its response. Variable assignment errors and missing message placeholders return 422, rolling back that turn's data and messages. Existing session/CSRF/role/ownership checks and revision-based workflow saves apply. See [SET_VARIABLE.md](SET_VARIABLE.md) for expressions, system variables, limits, and the complete example.
