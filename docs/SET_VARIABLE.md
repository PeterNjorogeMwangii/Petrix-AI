# Petrix AI — Set variable

Set variable creates or updates data in one chat session. It does not send a message. A later action can read the value, including Send message using `{complaint_ref}` or `{{complaint_ref}}`.

## Apply the update

Stop FastAPI and Angular, copy the updated project contents into your Petrix folder, and keep `backend/.env`, `.venv`, the database, and Mailpit. Python 3.9.12 remains supported. The current source includes new intent-matching dependencies; follow [INTENT_MATCHING.md](INTENT_MATCHING.md) to install them and configure the additional matching stages before restarting the services.

Set variable uses the conversation JSON storage added in migration `0003`. The current release also includes migration `0004` for permanent workflow step numbers. Run `python -m alembic upgrade head` from `backend` and verify `0004 (head)` before starting the API. See [WORKFLOWS.md](WORKFLOWS.md) for the upgrade commands and numbering behaviour.

## Configure and test

Open **AI agents → Workflows**, choose a workflow, select the desired **Add action** insertion point, then choose **Flow control → Set variable**.

| Setting | Behaviour |
| --- | --- |
| Variable name | 1–64 ASCII characters. Start with a letter; continue with letters, numbers, or underscores. Names are case-sensitive. |
| Chat script | A Python expression of up to 2,000 characters. Quote literal text; refer to existing variables by their bare names. |

Select **Save**. The action runs at its saved position when its parent trigger matches. Disabling it skips the action and its nested steps while preserving its configuration.

Create these four actions in sequence to try the complaint-reference example:

| Step | Action | Variable name | Chat script / message |
| --- | --- | --- | --- |
| 1 | Set variable | `financial_institution` | `"PetrixBank"` |
| 2 | Set variable | `ticket_number` | `"12345"` |
| 3 | Set variable | `complaint_ref` | `financial_institution + "-" + ticket_number` |
| 4 | Send message | — | `Your complaint reference is {complaint_ref}.` |

When placed under Chat started, restart the tester after saving. The reply is `Your complaint reference is PetrixBank-12345.` Open **Session variables** below the tester conversation to inspect the stored values. If the ticket number is numeric, use `financial_institution + "-" + str(ticket_number)` instead.

## Supported expressions

Values retain their types: text, finite numbers, Booleans, `None`, lists, and objects with string keys. Tuples become JSON lists for storage. Use Python spelling `True`, `False`, and `None`; the inspector displays JSON `true`, `false`, and `null`.

| Purpose | Chat script |
| --- | --- |
| Flag | `True` |
| Calculation | `round(amount * 0.16, 2)` |
| Counter with a default | `variables.get("attempts", 0) + 1` |
| Conditional value | `"approved" if score >= 700 else "review"` |
| Clean the incoming message | `message.strip().lower()` |
| Structured data | `{"name": agent_name, "verified": False}` |
| Read structured data | `customer["name"]` or `customer.get("name", "Unknown")` |
| Read a list item | `products[0]` |
| Format text | `f"{financial_institution}-{ticket_number}"` |

Supported operators: `+`, `-`, `*`, `/`, `//`, numeric `%`, unary signs, comparisons, membership checks, `and`, `or`, `not`, conditional expressions, indexing, and slicing. Text concatenation requires text operands; use `str()` for a number.

Functions: `str`, `int`, `float`, `bool`, `len`, `round`, `abs`, `min`, `max`, and `sum`. Use positional arguments. `int` and `float` accept one argument; rounding uses `round(value, precision)`. String methods: `lower`, `upper`, `strip`, `lstrip`, `rstrip`, `replace`, `split`, `join`, `startswith`, and `endswith`. Object `.get()` supports an optional default. Plain f-strings work; format specifications do not.

Chat scripts are expressions rather than full Python programs. The interpreter does not expose imports, files, networking, arbitrary functions or attributes, assignments, loops, comprehensions, lambdas, or object mutation. It interprets an allow-listed AST without using `eval`, `exec`, or `compile`.

## System variables

These names are available directly or through the read-only `system` dictionary, such as `system["agent_name"]`:

| Name | Value / access |
| --- | --- |
| `language` | Active session language. Writable; the result must be one of the agent's configured codes. |
| `agent_name`, `agent_id` | Saved agent name and current ID; read-only. |
| `default_language` | Agent's configured default language; read-only. |
| `conversation_id` | Current saved chat ID, including during Chat started; read-only. |
| `message` | Current event's text, or empty text on other events; read-only. |
| `feedback_rating` | Current CSAT rating or `None`; read-only. |
| `attachment_name`, `attachment_type` | Current attachment test's filename/category, or empty text; read-only. |

`variables` is a read-only dictionary of custom values. Use `variables.get("name", default)` when a value may not exist yet. System names, helper function names, and Python keywords cannot be custom variable names. The supported system write is `language`, for example a name of `language` and script `"sw"`. Following actions immediately use that language, and the tester follows it on later turns. The agent's saved default remains unchanged.

## Message placeholders

Both `{complaint_ref}` and `{{complaint_ref}}` work. Structured values support paths such as `{customer.name}` or `{products.0.name}`. Existing `{_chatbot.name}`, `{{agent.name}}`, `{{input.message}}`, and `{{feedback.rating}}` placeholders continue to work. Quick reply labels also support variables.

To display a value in bold in the tester, wrap its message placeholder in double asterisks, such as `Your reference is **{complaint_ref}**.` Variables are substituted by the backend before the assistant message is formatted for display.

Substitution is plain text and runs once. A variable containing another placeholder is displayed literally. Booleans, numbers, objects, and lists render as JSON; `None` renders as empty text. Unknown variables/paths produce a tester error instead of sending an unresolved placeholder. Rendered messages are limited to 4,000 characters and quick replies to 100 characters. Quick replies must remain distinct after substitution.

## Lifetime, errors, and limits

Values persist across turns, nested waits, unmatched events, and workflow revisions in the current conversation. A workflow edit clears obsolete execution continuations while retaining variables. Restarting the tester creates a new chat with fresh variables. Existing authentication, CSRF, workspace, Manage AI Agents, and conversation-owner checks protect access.

Invalid names and unsupported scripts are rejected when saving. Missing variables, invalid types, division by zero, unavailable keys/indexes, invalid languages, and size errors are reported when the step runs. Failed turns roll back that turn's assignments and messages; previous saved turns remain intact. Errors identify the step without echoing variable contents. Variables are not implicitly added to LLM prompts.

Each chat supports 100 custom variables using 64 KiB in total. A value uses up to 16 KiB; text up to 8,192 characters; lists/objects up to 100 items per container with eight nested levels. Numbers must be finite; integers have a 256-bit limit. Expressions allow 200 AST nodes, 20 levels of expression nesting, and a bounded operation count. Oversized repetitions, joins, and replacements are rejected before allocating the expanded result.

## Code and API

| Location | Purpose |
| --- | --- |
| `backend/app/workflows/actions/set_variable/config.py` | Name and expression validation. |
| `backend/app/workflows/actions/set_variable/handler.py` | Evaluate and assign; no message output. |
| `backend/app/workflows/chat_script.py` | Expression interpreter and JSON value validation. |
| `backend/app/workflows/context.py` | Session state, system variables, assignment policy. |
| `backend/app/workflows/templates.py` | Message and quick reply placeholders. |
| `frontend/src/app/features/workflows/actions/set-variable` | Dedicated settings component. |

The workflow node uses `kind: "action"`, `type: "set_variable"`, and config `{ "variable_name": "complaint_ref", "chat_script": "financial_institution + \"-\" + ticket_number" }`. Use the existing revision-checked workflow save API. `/tester/events` now returns a `variables` JSON object alongside messages, trace, language, and lifecycle state. Set variable appears in the trace without creating a message. Other unimplemented action types remain unavailable.
