# Petrix AI — Send API

Version `0.7.0` implements server-side HTTPS requests with **Success**, **Error**, and **Timeout** paths. Use a response in later Send message, Branch, Set variable, or Send API actions. The request action itself sends no chat message.

## Settings

Open **Workflows**, choose an insertion point, and select **Send API** under **AI & data**.

| Setting | Behavior |
| --- | --- |
| HTTP method | GET, POST, PUT, PATCH, or DELETE. GET sends no request body. |
| URL endpoint | An HTTPS URL, up to 2,048 characters in the saved configuration. Use variables in its path or query, for example `https://api.example.com/complaints/{complaint_ref}`. |
| Headers | Up to 20 optional key/value pairs. Names are case-insensitively unique. Values support variables, for example `Authorization` → `Bearer {access_token}`. Sensitive header values are masked in the editor and can be revealed. |
| Payload (JSON) | Shown for POST, PUT, PATCH, and DELETE. Choose JSON or Python expression. Empty means no body. A JSON content type is added unless you configure one. |
| Resulting variable | A custom chat variable, starting with a letter and using letters, numbers, or underscores, up to 64 characters. System names and Python keywords are reserved. |
| Request options → Timeout | Connection and read/write inactivity timeout, from 1 to 60 seconds; default 30. A slow response is also checked against elapsed time while its body is consumed. DNS resolution uses the host operating system's resolver limits. |

Select **Save**, then add actions under the outcome paths in the tree. A rejected save keeps your draft. Editing the request retains its outcome steps and their permanent numbers.

## Variables and payloads

Variables must exist before the action runs. URL values are substituted once and encoded so an answer cannot replace the endpoint's hostname, add query parameters, or change its path structure. Header values are substituted once and checked for line breaks and invalid characters.

For JSON mode, put placeholders inside JSON strings:

```json
{
  "reference": "{complaint_ref}",
  "customer_name": "{customer_name}"
}
```

The JSON is parsed before substitution. Quotes and backslashes in answers remain data, rather than changing the JSON structure. This mode retains the JSON literal's value types: a substituted JSON string stays a string.

Use Python expression mode for typed variables and objects:

```python
{"reference": complaint_ref, "amount": amount.norm.value, "confirmed": True}
```

Reference survey text with its bare name or `.text`, and survey numbers with their bare name or `.norm.value` in Python expressions. The result is encoded as JSON. Scripts use the existing bounded interpreter, without `eval`, imports, assignments, or arbitrary calls. JSON input supports up to 16,000 characters; Python expressions up to 2,000. Rendered data must fit the existing chat-value limits.

An access token can come from an earlier action. For example, if a token endpoint's resulting variable is `token_response`, set the next request's Authorization value to `Bearer {token_response.body.access_token}`. The token response must actually contain that field. Petrix does not obtain or invent authentication credentials automatically.

## Resulting variable

Every outcome replaces the previous result, including failures, so a failed request cannot leave a stale successful body.

For a variable named `complaint_status`, a successful JSON response looks like:

```json
{
  "ok": true,
  "status_code": 200,
  "body": {"status": "resolved", "reference": "REF-123"},
  "error": null
}
```

| Field | Meaning |
| --- | --- |
| `ok` | True only for Success. |
| `status_code` | HTTP status when available; otherwise null. |
| `body` | Parsed JSON for `application/json` or `+json` responses, text for other responses, or null for an empty response. It is null when the response cannot be retained. |
| `error` | Null on success, otherwise a controlled error code. Raw exception text, request headers, and credentials are not copied into this field. |

Arrays, objects, JSON scalar values, plain text, and empty bodies are supported. The result, including its envelope, must fit the existing 16 KiB variable limit, nesting limits, and 8,192-character text limit. Reading stops at 64 KiB of response bytes. Gzip decoding is bounded too. Responses that exceed these limits use Error without retaining a partial body. If the result fits one variable but exceeds the conversation's remaining 64 KiB total capacity, Error stores a small `variable_limit` result instead.

## Outcome paths

| Path | When it runs |
| --- | --- |
| Success | An HTTP 2xx response with a usable body. |
| Error | HTTP non-2xx, connection/TLS/DNS failure, blocked endpoint, invalid/oversized response, invalid request inputs, or a request/value limit. |
| Timeout | A connection/read/write timeout or elapsed response-reading budget. |

Only the selected path runs. It can contain its own actions, surveys, nested Branch or Send API actions, trigger alternatives, and local fallback. An empty path continues immediately. After the selected path finishes, execution joins the actions following Send API.

Surveys and nested triggers pause in the selected path; resuming them does not repeat the completed API call. Jump can enter Send API itself, which makes a new request, or a step inside an outcome path, which bypasses the request and starts at that step. Jump away abandons the current continuation. Disabled Send API skips the request and all outcome paths.

Common error codes are `http_error`, `network_error`, `timeout`, `request_error`, `blocked_endpoint`, `invalid_response`, `response_too_large`, `variable_limit`, and `request_limit`. Request preparation errors, such as missing URL variables, take Error without contacting the endpoint. A chat turn can make up to ten API calls; further calls in that turn take Error with `request_limit`.

## Complaint lookup example

1. Add Ask survey with a Text field and chat variable `complaint_ref`.
2. Add Send API with method GET, URL `https://api.example.com/complaints/{complaint_ref}`, and resulting variable `complaint_status`. Add authentication headers if the real endpoint requires them.
3. Under Success, add Branch with condition `complaint_status.body.status == "resolved"`. Configure its messages for resolved/open results.
4. Under Error, add Send message: `We couldn't check your complaint right now. Please try again.`
5. Under Timeout, add Send message: `The service took too long. Please try again shortly.`

For a simpler Success response, add Send message using `Complaint {complaint_status.body.reference}: {complaint_status.body.status}.` Use field names actually returned by your API. The example host is a placeholder, not a supplied complaint service.

## HTTPS and private business systems

Requests originate from the Petrix backend. The destination must be reachable from that server and have a trusted TLS certificate. Certificates are verified against the original hostname, even though the connection is pinned to a validated DNS address. Redirects are returned as Error rather than followed. There are no automatic request retries, ambient proxy use, or shared browser/session cookies. Configure explicit headers for the external service.

Public HTTPS hosts require no additional setting. To use an internal banking or business API, an operator can add exact approved hostnames or IP addresses to the existing `backend/.env` and restart FastAPI:

```dotenv
SEND_API_ALLOWED_PRIVATE_HOSTS=core-api.bank.internal,10.20.30.40
```

This enables private addresses for those exact hosts; subdomains and wildcard patterns are not implied. Metadata/link-local, unspecified, multicast, and reserved destinations remain blocked. Every resolved address is checked before any request is sent, preventing mixed public/private DNS answers from bypassing the policy. URL credentials, dynamic hostnames, fragments, and transport/proxy headers are rejected.

An external POST/PUT/PATCH/DELETE can change another system. Petrix's database rollback cannot undo an external change if a later workflow action fails. Use the destination's idempotency mechanism for operations that must tolerate a user retry or repeated workflow execution. No automatic retry is introduced by this action.

## Storage and API

The workflow node has `kind: "action"`, `type: "send_api"`, empty `children`, and three `paths` containers. Config fields are `method`, `url`, `headers: [{key,value}]`, `payload_mode`, `payload`, `variable_name`, `timeout_seconds`, `success_path_id`, `error_path_id`, and `timeout_path_id`.

Generate distinct UUIDs for each outcome and include exactly one matching `paths: [{id,children}]` entry per outcome. The server stores them in Success/Error/Timeout order. Child nodes use ordinary permanent step numbers; path containers are unnumbered and cannot be Jump destinations. Existing limits of 500 steps, 12 nested levels, and 1 MiB per workflow apply across all paths.

Use the existing revision-checked workflow save endpoint. Existing membership, Manage AI Agents, CSRF, conversation ownership, rate limiting, and conflicting-save checks apply. Tester trace records `type: "send_api"`, the selected `path_id`, `outcome: "success" | "error" | "timeout"`, `status_code`, and `error`, alongside the step UUID/number. Response bodies remain in the configured chat variable.

No migration or new dependency is required. Schema head remains `0005`; HTTPX and the TLS libraries are already pinned. Keep your current environment files and database, copy the source update, then restart FastAPI and Angular. See [WORKFLOWS.md](WORKFLOWS.md) if upgrading from an earlier schema.
