# Ask survey

Ask survey sends preset questions as regular chat messages, waits for each answer, validates it, and saves it in a chat variable. When the last field is answered or skipped, the workflow continues through the survey's nested steps and following actions.

## Configure the action

Open a workflow, choose **Add action → Messaging → Ask survey**, then use **Add field** for each question. Fields run from top to bottom. Expand a field to edit it, or use its delete icon to remove it before saving.

| Setting | Behavior |
| --- | --- |
| Question | Required question text, up to 4,000 characters. Existing variable placeholders work, including answers to earlier fields. |
| Answer must be | Text, Number, Attachment, Attachment(s), Email, or Phone. |
| Invalid answer reply | Required reply shown when validation fails. The same question remains active. |
| Chat variable name | A unique custom variable per field; 1–64 characters, starting with a letter, followed by letters, numbers, or underscores. System names and Python reserved names cannot be used. |
| Quick answers | Up to 13 optional, distinct buttons of 1–100 characters each. These offer choices without restricting other valid answers. |
| Enable skip | Allows the person to type `skip` or choose **Skip question** in the tester. The variable is set to null and the next question is asked. |

Click **Save** after filling the required settings. A survey supports 1–50 fields. Question text, invalid replies, and quick answers support the same variable placeholders as Send message. The editor does not automatically translate these texts.

## Answer formats

| Format | Saved value |
| --- | --- |
| Text | Non-empty text with surrounding whitespace removed. |
| Number | A finite JSON number; integers remain integers. Signed decimals and scientific notation are accepted. Grouping commas, NaN, infinity, and numbers outside the supported range are rejected. |
| Email | A syntactically valid email address, with normalized domain casing. No DNS/delivery verification is performed. |
| Phone | A string of 7–15 digits, optionally starting with `+`. Spaces, parentheses, dots, and hyphens are removed. No country inference or ownership verification is performed. |
| Attachment | One uploaded file represented by an object with `id`, `name`, `content_type`, `size_bytes`, and an authenticated download `url`. |
| Attachment(s) | One to ten uploaded files submitted together, stored as a list of those objects. |

File limits are 10 MiB per file and 20 MiB per answer. Files must not be empty. File bytes are stored in PostgreSQL with the conversation; only metadata enters the chat variable. The download endpoint checks workspace, agent, and conversation ownership, and delivers files as downloads. Original filenames are normalized to remove path components.

During a file question, use **Upload a file** or **Upload files together**, then **Send files**. Sending one answer never reuses those files for the next question; the picker resets. Text answers to file questions produce the configured invalid reply. The existing **Test an event → Attachment received** control simulates metadata only and cannot satisfy a survey's real upload requirement.

## Example

Create a survey with:

| Field | Question | Format | Variable |
| --- | --- | --- | --- |
| 1 | What is your name? | Text | `customer_name` |
| 2 | What is your email address? | Email | `email` |
| 3 | Upload your document. | Attachment | `document` |

Follow it with a Send message action:

```text
Thank you, {customer_name}. We have recorded your email as {email}.
```

Later actions can read `{document.name}` or `{document.url}`. For multiple files, paths such as `{documents.0.name}` are supported. A Jump after the confirmation can return to an existing menu.

## Conversation behavior

- A pending survey receives message/file answers before intent matching, fallback, or generative replies. There are no model calls to classify its answers.
- Each valid answer is saved before the next question is sent. Invalid answers do not advance or overwrite a variable.
- Skipping stores null, including when that variable held a value from a previous visit. Without skip enabled, `skip` is treated as a normal answer and validated against the format.
- The final answer resumes the exact saved branch continuation. Consecutive surveys and nested triggers remain sequential; a final answer is never consumed as the next survey's first answer.
- Closing or timing out the chat cancels the pending survey and runs the relevant lifecycle workflow. Completed answers remain in that conversation's variables.
- A saved workflow change invalidates the pending answer token. Restart the tester after editing a running survey.
- A failed downstream action rolls back the entire answer turn, including file bytes, messages, and variable changes. The original question remains active for retry.

Questions, answers, skips, and validation replies are persisted as regular messages. Channel-specific auto-fill buttons, external channel publishing, and subscription interaction billing are separate integrations; this update provides the workflow builder and authenticated tester.

## Apply this update on Windows

Keep your existing `backend/.env`, virtual environment, Mailpit, and database. Stop the API, copy the updated source into your project, then run from **backend**:

```powershell
python -m alembic upgrade head
python -m alembic current
python -m uvicorn app.main:create_app --factory --reload --port 8000 --no-access-log
```

The migration check should show `0005 (head)`. Migration `0005` adds `agent_attachments`; it does not reset existing workflows, numbers, accounts, or conversations. No new dependency is required for Ask survey. Restart Angular with your existing `npm start` command.

## API

The prefix remains `/api/workspaces/{workspace_id}/agents/{agent_id}`.

| Endpoint | Contract |
| --- | --- |
| `POST /workflows` | The existing revision-checked whole-tree save. An `ask_survey` node has `config.fields[]` with the field settings described above and a stable field UUID. |
| `POST /tester/events` | Survey text answers use `event: message_received`, `content`, `conversation_id`, and the current `survey_answer_token`. |
| `POST /tester/attachments` | Multipart `conversation_id`, `survey_answer_token`, optional `language`, and one or more `files` parts. Upload and answer processing are atomic. |
| `GET /tester/attachments/{id}` | Authenticated download for the same conversation owner and agent/workspace. |

Tester responses include `survey` (or null), progress, the expected format, skip permission, and the answer token. Send that token with each answer. A stale/replayed token returns 409 and does not advance the survey. File responses also include `attachments[]`; the tester displays their download links as regular user messages.

Implementation lives in `backend/app/workflows/actions/ask_survey`, `backend/app/workflows/attachments.py`, the existing workflow runtime/routes, and `frontend/src/app/features/workflows/actions/ask-survey`.
