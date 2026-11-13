# Petrix AI — Branch

Version `0.6.0` adds Branch to **Flow control**. It evaluates ordered Python expressions against the current chat data and runs the first matching path. If every condition is false, it runs Else. The Branch action itself sends no message.

## Configure a Branch

1. Open your agent's **Workflows**, choose a workflow, and select **Add action → Branch**.
2. Name the action. Give the first **If** condition a name and a Python expression.
3. Use **Add condition** for additional **Else if** paths. A Branch accepts 1–10 conditions, plus one permanent Else path. The arrows change evaluation order; the delete control removes a condition and its nested steps.
4. Select **Save**. The workflow tree displays each condition and Else separately. Add actions or nested triggers under the appropriate path.
5. Restart the tester and supply the data the conditions need. **Last execution** shows the chosen path.

Names are up to 100 characters; expressions are up to 2,000 characters. Empty names, empty expressions, invalid Python, and unsupported operations are rejected on save. A rejected save retains the draft. Deleting a populated condition asks for confirmation. A referenced Jump destination must be updated or removed before its path can be deleted.

## Variables and expressions

Use existing variables without curly brackets. Comparisons are case-sensitive unless you normalize the text explicitly.

| Source | Condition example |
| --- | --- |
| Ask survey · Text | `complaint_status.text == "resolved"` |
| Ask survey · Number | `score.norm.value >= 5` |
| Set variable | `customer_status == "active"` |
| Send API response | `api_response.body.customer.tier == "gold"` |
| A JSON object stored with Set variable | `customer.tier == "gold"` |
| Extracted intent entity | `system.intent.entities.get("city") == "Nairobi"` |
| Optional or skipped answer | `score is not None and score.norm.value >= 5` |
| Variable that may not exist | `variables.get("customer_status") == "active"` |

Survey answers continue to be stored as plain text, numbers, file objects/lists, or `None`. The expression interpreter supplies read-only `.text` and `.norm.value` views for text and number values, including answers collected before this update. Existing expressions such as `score >= 5` and message placeholders such as `{score}` keep working. These scalar views also work with values assigned by Set variable; using the bare name remains supported.

Public dotted fields resolve JSON object keys only. Use `object["key"]` or `.get("key", default)` for keys that are not Python identifiers or need a default. The interpreter never reads Python object attributes. Private attributes, imports, assignments, loops, arbitrary function calls, filesystem access, and networking remain unavailable. The same bounded Python expression interpreter is shared with Set variable; see [SET_VARIABLE.md](SET_VARIABLE.md).

Conditions follow Python truthiness. Evaluation stops at the first truthy result; later conditions are not evaluated. Use `and`, `or`, and `not` for combined checks. A missing variable, invalid type, or other evaluation error returns a useful step/condition error and rolls back that turn's messages, survey answers, and variable writes. An error does not silently take Else. Previously committed turns remain intact.

## Complaint example

After Ask survey collects a text answer into `complaint_status`, add:

| Path | Expression | Action |
| --- | --- | --- |
| If · Resolved | `complaint_status.text == "resolved"` | Send message: `Good news – this has been resolved.` |
| Else | — | Send message: `Your complaint needs further review.` |

Add shared follow-up actions after the Branch row. After the selected path finishes, those actions run once. Live agent handoff remains a reserved catalogue action; this update implements Branch and does not simulate an external transfer.

## Execution and Jump

- Only the selected path runs. An empty selected path continues immediately to the action after Branch. Disabling Branch skips all its paths.
- A nested Branch works inside any condition or Else path. Each path can have its own nested trigger alternatives and fallback.
- Ask survey pauses within the selected path. Its final answer resumes that path and then the enclosing actions. Nested triggers likewise wait for the next matching event.
- Jump can target Branch itself or any saved trigger/action inside its paths. Jump to Branch evaluates its conditions. Jump directly to a contained step starts there without evaluating the enclosing conditions, continues within that destination path, and then runs its enclosing continuations. Other paths are not entered.
- Jump away from a path abandons its remaining actions. Existing loop protection still applies.
- Workflow edits clear obsolete waiting continuations on the next event. Restart creates a new conversation with fresh variables.

Existing limits of 12 nested levels, 500 workflow steps per agent, and 1 MiB per definition apply across all condition and Else paths. Path containers do not have step numbers or act as Jump destinations; the triggers and actions inside them receive ordinary permanent numbers. Renaming/reordering conditions preserves their contained steps and numbers.

## API storage

Use the existing revision-checked `POST /api/workspaces/{workspace_id}/agents/{agent_id}/workflows` endpoint. A Branch node has `kind: "action"`, `type: "branch"`, empty `children`, and:

```json
{
  "config": {
    "conditions": [
      {"id": "10c81b74-edbe-43f1-b5a6-aeaf6240762b", "name": "Resolved", "chat_script": "complaint_status.text == \"resolved\""}
    ],
    "else_path_id": "2de0e86a-e4c8-411b-bca7-e8c51705798e"
  },
  "paths": [
    {"id": "10c81b74-edbe-43f1-b5a6-aeaf6240762b", "children": []},
    {"id": "2de0e86a-e4c8-411b-bca7-e8c51705798e", "children": []}
  ]
}
```

Generate unique UUIDs for nodes and path containers. Each condition and Else must have exactly one matching path. The server canonicalizes paths into condition order followed by Else and validates every contained step, reference, and number. Other node types retain their existing shape. Tester trace entries use `outcome: "branched"`, `path_id`, and `condition_name`, alongside the Branch's `node_id` and `node_number`.

## Apply the source update

Keep your existing `backend/.env`, virtual environment, database, and `node_modules`. Copy the updated source and restart FastAPI and Angular. Branch introduces no dependency or migration; schema head remains `0005`. If upgrading from before Ask survey, first run `python -m alembic upgrade head` from `backend` as described in [WORKFLOWS.md](WORKFLOWS.md).
