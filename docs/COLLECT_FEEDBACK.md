# Collect Feedback — Rating, Survey, and Settings

Add **Collect feedback** beneath the permanent **Chat closed** trigger. Configure Rating, Survey, and Settings, choose each Content language as needed, and press Save. The current release supports the complete interaction in the authenticated Agent tester.

## Rating tab

| Setting | Behavior |
| --- | --- |
| Enable rating | Displays the 1–5 scale when on. Turn off to collect only survey answers. |
| Webchat rating title | Default `Please rate the conversation`; 1–500 characters while rating is enabled. A live preview displays five vector face icons. |
| Rating request message | Default `How would you rate your conversation?`; up to 4,000 characters. Used in the non-Webchat channel previews. |
| Set chat variable | Default `feedback_rating`. Stores the accepted integer 1–5 in a new or existing chat variable. The copy button copies the name. |

The Webchat preview uses keyboard-operable face choices. The Messaging buttons preview displays numeric buttons 1–5. Bitrix24 and Africa’s Talking previews use a typed number. **Channel preview** in the tester restarts a new test conversation; it does not connect or publish a channel. The response exposes the appropriate prompt/control metadata for future channel adapters.

## Survey tab

Enable survey and press **Add field**. Each field has:

| Setting | Behavior |
| --- | --- |
| Question | Required text, up to 1,000 characters. |
| Required field | The customer must answer when checked. Unchecked fields can be left blank and save `None`. |
| Reply type | Open response, Number, Email, Phone, or Multiple choice. |
| Webchat field placeholder | Optional translated hint, up to 200 characters. |
| Choices | Multiple choice only; 1–13 distinct options, up to 100 characters each. |
| Create variable | Stores the submitted answer in a named chat variable. The first new field defaults to `feedback_comment`. |

Up to 50 fields are supported. Expand/collapse a field from its header; use up/down buttons to reorder, or the delete icon to remove it. Reordering preserves field IDs, variable names, and all translations. Deleting removes that field’s translations too.

Open response saves trimmed text. Number saves an integer or finite decimal number. Email saves a validated, normalized email; Phone removes presentation spaces/brackets/hyphens and validates 7–15 digits with an optional `+`. Multiple choice accepts only one of the issued translated choices. Each submitted answer is limited to 4,000 characters. Invalid answers display an error, retain the form entries, and allow correction without consuming the request.

Feedback variable names must start with a letter and contain only letters, numbers, or underscores, up to 64 characters. Python keywords, interpreter functions, `language`, and reserved system names are unavailable. Rating and survey fields must use distinct names. The conversation’s existing 100-variable / 64-KB limit also applies. New answers replace existing values with the configured names.

## Settings tab

**Feedback submission text** is required while rating or survey is enabled; default `Thank you for your feedback!`, up to 4,000 characters. It is displayed after the form is accepted. It supports existing text placeholders, including newly saved values, for example `Thank you! Your rating was {feedback_rating}.`

The confirmation appears separately from ordinary chat messages, and normal chat stays closed. A failure in following workflow actions still leaves accepted answers saved. The tester shows the failure explicitly.

## Localisation

Use **Content language** to provide each configured language manually. Rating titles, request messages, survey questions, placeholders, choice labels, and submission text are translated independently. Field identity, reply type, required flag, variable names, ordering, and feature toggles are shared across languages.

Rating/request/confirmation text uses active-language, agent-default, then first configured language fallback. Each survey field follows that same fallback independently, so a missing question translation can use its default-language version without changing its ID or stored variable. Viewing an unconfigured language creates no translation; editing text creates one. Every configured language needs submission text and, when rating is enabled, both rating prompts. Each active survey field needs a question in at least one language. Choice fields need choices in each provided question translation.

Questions, prompts, placeholders, and choices can use already-collected chat variables. Submission text resolves after feedback is stored and can use the new answers. Text is rendered safely as text. Workflow actions do not translate content automatically. Legacy 0.12.0 rating configurations gain default request/confirmation text; translate those defaults for your supported languages.

## Two flows to test

For a rating and optional comment, use the complete configuration in [CHAT_CLOSED.md](CHAT_CLOSED.md), then add a Branch condition `feedback_rating >= 4`. Add a Ready Close chat action to your ordinary workflow to invoke this automatically, or use the manual close test event. Submit 5 with `Very helpful` and check both variables and the If path. Restart and submit 2 with a comment to check Else. A duplicate submission is rejected.

For comments only, turn **Enable rating** off and **Enable survey** on. Create a required Open response question `What could we improve?`, placeholder `Share your suggestions`, variable `feedback_comment`, and submission text `Thank you. Your suggestion has been recorded.` After Collect Feedback, add Set Variable with name `comment_copy` and Chat script `feedback_comment`. Close the tester and submit a comment: both variables contain it, and no rating is requested or required. Leaving the required answer blank prevents submission.

Turning both features off preserves their settings and executes no feedback request; the workflow continues immediately. Disabling the action skips it entirely. No unsolicited feedback is accepted for either case. Once an enabled form is issued, its one-use token and question snapshot remain valid for that conversation even if the saved configuration changes; remaining actions are not resumed against a changed workflow revision.

No new dependency or migration is needed. Schema head remains **0007**. Live channel transport, inbox/live-agent controls, and analytics screens are later features; all channel variants above are tester previews.
