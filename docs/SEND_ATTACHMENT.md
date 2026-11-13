# Send Attachment

Send one predefined file as an assistant message, then continue through the action's children and following steps. This action works inside Branch and API/SQL outcome paths and can be reached by Jump. It does not pause for a reply unless you add a nested trigger or an Ask survey action.

## Configure the action

1. Open an agent's **Workflows**, open a trigger, and choose **Add action → Messaging → Send attachment**.
2. Choose **Content language** from the languages already configured for that agent.
3. Choose a **File format** and **Source**, provide the file, and click **Save**.

| File format | Extensions |
| --- | --- |
| Image | jpg, jpeg, png, gif |
| Audio | mp3, m4a, wav |
| Video | mp4, avi, mov |
| Document | pdf, csv, doc, docx, xls, xlsx, ppt, pptx |

| Source | Configuration |
| --- | --- |
| Upload file | Browse or drop one non-empty file, at most 20 MiB (20,971,520 bytes). The backend checks extension and basic file contents; images also undergo image validation. Files are retained in PostgreSQL. |
| File link | A complete HTTP or HTTPS file URL, at most 2,048 characters. Query strings are allowed. Credentials, fragments, placeholders, whitespace, and non-web URL schemes are rejected. A recognised extension must match the selected format; extensionless endpoints and download-handler URLs are accepted, using a generic filename for the selected format. |
| Chat script | A safe Python expression returning a file URL string. Examples: `'https://example.com/forms/complaint.pdf'`, `file_url`, or `uploaded_form.url` from an attachment survey answer in this conversation. No curly-brace substitution is performed by this action. |

Hosted links are delivered directly to the browser; the backend does not fetch, proxy, inspect, or store their contents. The 20 MiB upload limit cannot be checked for a hosted URL. Use an accessible direct file URL and the correct format. Hosted files must remain available for as long as the link is needed; their contents may change outside Petrix AI. Authentication required by the host still applies.

The tester displays uploaded images and native audio/video controls, plus a download card. Documents receive a download card. Hosted files receive an **Open file** card; media also receive a preview. Actual audio/video playback depends on the browser's container and codec support, particularly AVI and MOV. A browser that cannot preview the media can still open or download it.

## Languages

Configure each language independently with the **Content language** dropdown. English and French can use different filenames, formats, or sources. Merely viewing an unconfigured language does not create an empty translation.

The runtime selects the active conversation language, then the agent's default language, then the first saved attachment. As with Send message, a missing translation uses this fallback. Provide all required language files before relying on a multilingual flow. **Switch Language does not translate file contents.** Stored translations remain in the action when an agent's configured languages change.

## Complaint form example

1. Add a **Message received** trigger named `Complaint form`, with exact English example `complaint form`.
2. Add **Send message**: `Here is the complaint form. Please complete it and keep a copy.`
3. Add **Send attachment**, select **Document → Upload file**, and upload `complaint-form.pdf`.
4. Add **Send message**: `Would you like help completing this form?`, with quick replies `Yes` and `Main menu`.
5. Add appropriate nested message triggers for those replies. Under `Main menu`, use **Jump** to your existing menu action.
6. Save, restart the tester, and send `complaint form`. You should receive a message, a downloadable PDF, and the follow-up question in that order.

To test localisation, configure French on the agent, add `formulaire de réclamation` to the trigger's French examples, provide the French messages and a French attachment, and put **Switch Language → French** before jumping to the shared menu or form trigger. The same attachment action will then send the French file.

## Chat script examples

Use a variable containing a URL:

```python
# Set variable: variable_name = file_url; chat_script = the expression below
'https://example.com/forms/complaint.pdf'
```

Then configure Send attachment as **Document → Chat script** with `file_url`.

To return a file collected by **Ask survey**, create an Attachment field named `uploaded_form`. Follow it with Send attachment, choose the matching file format and enter `uploaded_form.url`. Only actual files belonging to this conversation can be accessed through a relative survey URL. Another conversation's file, a workflow asset URL, and arbitrary server paths are rejected.

An expression returning a number, dictionary, missing variable, invalid URL, or mismatched extension produces a controlled tester error. The whole turn rolls back, including generated messages and newly copied file bytes. At most ten attachment actions may execute in one turn, including through Jump; this complements the existing workflow step limit.

## Storage, access, and upgrade

Apply migration **0007** from the backend directory before starting this version:

```bash
python -m alembic upgrade head
```

The migration adds `workflow_assets` and raises the conversation-file storage constraint to 20 MiB for sent copies. Survey answers keep their existing 10 MiB per-file and 20 MiB per-answer limits. Existing accounts, settings, workflows, step numbers, and survey files are retained. Keep your existing `backend/.env` and database.

Uploads are immutable, scoped to an agent and workspace, and require Manage AI Agents permission and CSRF protection. Workflow saves verify every upload reference and its selected format, including disabled steps and outcome paths. Unused draft/replaced uploads are deleted by the editor when discarded, saved, or removed; the server refuses to delete files still referenced by the saved workflow. Each agent can store up to 100 uploaded assets and 1 GiB in total. An interrupted browser session may leave an unused upload; the authenticated deletion endpoint can remove it once it is no longer referenced.

When an uploaded asset is sent, the bytes are copied into an immutable conversation attachment in the same transaction as its assistant message. Replacing or deleting the workflow asset cannot change earlier sent downloads. Conversation downloads are restricted to the conversation's user, workspace, and agent. Readable workflow previews require workspace membership. Media URLs support HTTP byte ranges. Documents are served as downloads, and uploads do not allow SVG, HTML, or executable formats.

The action emits one assistant attachment message per file. Billing/interaction deductions and delivery through published messaging channels are not implemented by this tester release. Agent configuration exports do not yet package workflows or their uploaded files; retain database backups for these assets.
