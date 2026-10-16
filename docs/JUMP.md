# Jump

Use **Jump** to redirect a conversation to an existing trigger or action in this agent's workflow tree. For example, send a complaint confirmation and then return to the same Main menu action used elsewhere.

## Configure

1. Open the source workflow and click **Add action** at the desired position.
2. Choose **Flow control → Jump**.
3. Open **Jump to**. Search by step name, `#number`, or parent workflow name.
4. Select a destination trigger or action. The selected card shows its permanent number, name, and location. Use the × button to clear it and select another.
5. Click **Save**. The source row shows `Jump to #number Name`.

Number badges identify each saved step's creation order and survive renaming and reordering. The picker lists steps across all this agent's workflows, including nested triggers and actions. Save a new destination first so it has an identity and number. Disabled destinations or destinations under disabled parents are unavailable to an enabled Jump. A Jump cannot select itself.

## Example

Create a Send message action named **Main menu**, with quick replies and nested Message received triggers for the available choices. Keep the Main menu action's saved number, for example `#12`.

In the complaint branch, add:

```yaml
Send message: "Your complaint has been logged."
Jump to: "#12 Main menu"
```

The confirmation is sent, then the existing menu message is sent and the conversation waits for its nested choice triggers. Each choice can return to that same menu again after new input.

## Execution rules

- An action destination executes that action, its children, and its following sibling steps. Earlier siblings and ancestor actions are not replayed.
- A trigger destination enters that trigger's children immediately. Its matching examples do not have to match the current input. Other sibling triggers remain alternatives.
- Destination execution keeps its enclosing action continuations, as if execution had reached that point normally. Nested triggers still wait for the next matching event.
- Jump discards the source branch's remaining stack. Children and following steps at the source Jump are not executed through that source path.
- Variables and the active language are retained. Jump itself sends no chat message.
- Jumping to a system trigger runs its configured actions. It does not synthesize a new chat-started, closed, timeout, or feedback event; the actual tester event continues to control conversation lifecycle flags.
- Running the same Jump again within one event is treated as a loop and returns a clear validation error. Waiting for new input permits returning through the same Jump in a later event. Failed events commit no partial messages or variable changes.

Update or remove incoming Jump actions before deleting their destination. Disable an incoming Jump, or its enclosing branch, before disabling its destination. The backend checks the whole submitted tree atomically and rejects missing targets and targets belonging to another agent. Runtime validation also catches damaged stored references.

## API and files

The existing revision-checked `POST /api/workspaces/{workspace_id}/agents/{agent_id}/workflows` endpoint saves the whole tree. A Jump action's settings are:

```json
{
  "target_node_id": "5f315f74-a965-40b7-b681-070571bb8925"
}
```

Use the destination's actual UUID from `GET /workflows`; the UUID above is illustrative. Names and badge numbers are display values. The backend validates the UUID and resolves it only inside the same agent's submitted tree. Normal authentication, permissions, CSRF, revision checks, and change logging apply.

Tester traces show the Jump's source number, `Jumped to #number`, and the destination's executed steps. Trigger destinations have an additional `Entered by Jump` trace entry. Raw Jump trace records include `target_node_id` and `target_node_number`.

| File | Purpose |
| --- | --- |
| `backend/app/workflows/actions/jump/config.py` | Required UUID target schema |
| `backend/app/workflows/actions/jump/handler.py` | Control transfer without producing a chat message |
| `backend/app/workflows/service.py` | References, availability, and deletion checks |
| `backend/app/workflows/runtime.py` | Destination stack, nested waits, and loop protection |
| `frontend/src/app/features/workflows/actions/jump/` | Searchable picker, selected card, and validation |

## Apply this update on Windows

Stop FastAPI and Angular, copy this archive's source into your current `petrix-ai` folder, and keep your existing environment files, virtual environment, database, and `node_modules`. Restart both services with your usual commands. No new Python or npm dependencies are introduced by Jump. The current bundle also includes Ask survey: run `python -m alembic upgrade head` from backend to reach `0005` before using file questions.

Test by completing a complaint, confirming that the menu reappears, and selecting another menu choice. In **Last execution**, verify that the Jump points to the saved menu number.
