# Close Chat — version 0.14.0

**Close chat** is Ready under **Add action → Flow control**. It closes an open conversation and runs the permanent Chat Closed workflow. There are no action-specific settings: enter an optional name/description, leave Enabled on, and Save.

## Example: end the chat and collect feedback

1. Under a Message received trigger such as `done`, add Send message with `Thank you for contacting us. Goodbye!`.
2. Add **Close chat** after that message and Save.
3. Open the permanent **Chat closed** workflow and add **Collect feedback**. Configure its Rating, Survey, and Settings tabs as described in [COLLECT_FEEDBACK.md](COLLECT_FEEDBACK.md).
4. Restart the Agent tester and send `done`.
5. The goodbye appears, the conversation becomes Closed, and the configured feedback form is displayed. Submit a rating/comment to save its variables and resume the actions after Collect Feedback.

The action closes the chat automatically; you do not need to run the manual Chat closed test event. It does not generate a goodbye message itself. Normal replies and old quick replies stay disabled after closure. Restart starts a new test conversation.

## Flow behavior

For an open conversation, Close chat stops the current workflow, including its own children, later siblings, and enclosing continuations. It works in ordinary workflows, Branch paths, API/SQL outcomes, after a completed survey, or as a Jump destination. A disabled Close chat skips its subtree and lets the open flow continue.

The permanent Chat Closed trigger then runs once. Its existing action restrictions and safe Jump rules still apply. Collect Feedback can pause that post-close workflow until submission. Variables collected before closure and the active language are retained.

If Close chat runs when the conversation is already closed—for example beneath Chat Closed or after feedback—it is an idempotent no-op. It traces Already closed, preserves existing feedback/closure metadata, and continues the allowed actions without firing Chat Closed again. This prevents a recursive closure loop.

## Persistence and errors

The incoming turn, earlier messages/media, current variables, and closure are committed before the Chat Closed automation runs. A later automation error leaves the chat Closed, preserves the earlier goodbye and files, and reports the failure in the tester. Changes made by that failed post-close automation are rolled back. An error before reaching Close chat retains the existing whole-turn rollback behavior and does not close the chat.

Workflow closure records `close_source:"workflow"`, the triggering user/time, and the Close chat node ID/number. The explicit tester close event continues to record `close_source:"tester"`. Repeated close events and ordinary replies are rejected after closure; they do not replay external effects. External API side effects cannot be undone automatically.

The tester supports this end-to-end lifecycle. Livechats/Inbox controls and published-channel transport remain separate milestones. This action adds no migration or dependency; **schema head remains 0007**. Preserve the existing `.env` and database, replace source, and restart backend/frontend.
