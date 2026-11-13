# Switch Language

Version 0.9.0 adds a workflow action that changes the current conversation's active language. It uses languages already configured in that agent's Agent settings. It does not change the agent's default language, affect another conversation, or send a message by itself.

## Configure the action

1. In the agent's **Agent → Languages** settings, enable the languages you need (for example English and French) and save.
2. Open **Workflows**, choose a trigger, and select **Add action → Flow control → Switch Language**.
3. Select **Target language** and press **Save**. The selector contains only that agent's configured languages and initially selects its default language.
4. Add the next actions as children or following siblings. Prepare their messages and trigger examples in the target language.

The action has the same name/description/enabled controls, Save behavior, permanent numbering, role permissions, and unsaved-draft protection as the other workflow actions. It can be nested under Branch/API/SQL paths or reached through Jump.

## English/French menu example

1. Configure English and French on the agent, with English as its default.
2. In the Main menu **Send message**, add an English translation with `Welcome. How can I help you?` and quick answers `French`, `English`, and `Help`. Add a French translation with `Bonjour. Comment puis-je vous aider ?` and quick answers `Français`, `English`, and `Aide`.
3. Under Main menu, add a **Message received** trigger called Choose French. Use example `French` in English and `Français` in French.
4. Under Choose French, add **Switch Language → French**, then a Send message with French translation `Langue changée en français.`, then Jump to Main menu.
5. Add Choose English with example `English` in both languages. Add **Switch Language → English**, an English confirmation `Language changed to English.`, and Jump to Main menu.
6. Add a Help trigger with English example `Help` and French example `Aide`. Give its response both translations, then Jump to Main menu.

Restart the tester in English and send `French`. Expect the French confirmation and French menu, including French quick replies. The tester's Language selector becomes French. Send `Aide` on the next turn; the French trigger example and French response are used. Send `English` to switch back. The execution trace shows the previous and selected languages.

## Runtime behavior

The action writes the existing conversation-level `language` state. Subsequent Send message actions select the matching translation, and subsequent turns match triggers in the saved language. The value is available to Branch and chat scripts as `language` and `system.language`, and to message/API/SQL templates as `{language}`. It is not an extra custom variable.

A survey after the switch uses the selected chat language and retains it when answering/resuming. Survey questions themselves are configured text; write the questions in the intended language. Jump uses the selected language at its destination. Generative fallback requests now explicitly include the active language in their system instruction. Actual model language adherence depends on the configured provider; this update does not add a provider or call one automatically.

Switching to the already active language is allowed. An explicit tester Language change can override the saved chat language on a later event. A new conversation uses the agent's default unless a language is explicitly supplied by the tester. Existing Set variable assignments to `language` remain supported.

Switch Language does **not** automatically translate authored messages, survey questions, or intent examples. Existing Send message behavior falls back to the agent's default/available translation if the selected one is missing. Add the relevant translations and trigger examples for the experience you want.

## Validation and data preservation

Workflow saves reject unsupported or unconfigured targets, including those in nested/disabled paths. Runtime checks configuration again and rejects an unavailable target without committing a partial language change. All changes in a failed turn roll back with that turn's messages and variables.

To remove an agent language, first update or remove Switch Language actions targeting it, including disabled actions. The same guard applies to restoring an older agent-settings version, so restoring settings cannot silently strand a saved language switch.

No new dependency or database migration is required by version 0.9.0. Schema head remains `0006`; existing accounts, settings, workflows, step numbers, and conversations are preserved. Keep your `.env` and restart the API and frontend after copying the updated source. If upgrading from before SQL Query, run `python -m alembic upgrade head` from backend as described in [DATABASE_CONNECTIONS.md](DATABASE_CONNECTIONS.md).

## API configuration

```json
{
  "type": "switch_language",
  "config": {"target_language": "fr"}
}
```

The value is the configured language **code**, such as `fr`, rather than its display name. Standard workflow-node fields still apply. The action uses ordinary `children`, not outcome paths. The tester returns the active language in its existing top-level `language` field and adds `previous_language` / `target_language` to this action's trace entry. No assistant message is created by the switch itself.
