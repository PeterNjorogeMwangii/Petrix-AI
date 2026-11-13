# Show Carousel

Show Carousel sends one assistant message containing a horizontally scrollable set of cards. Each language can have **1–10 cards**, with **0–3 buttons per card**. Cards can have an optional uploaded or hosted image, a required title, and an optional subtitle.

## Configure cards

1. Open an agent's **Workflows**, open the relevant trigger, and choose **Add action → Messaging → Show carousel**.
2. Choose **Content language**. Expand **Card #1** to edit it.
3. Enter a title (up to 80 characters) and optional subtitle (up to 200 characters).
4. For **Image**, choose **No image**, **Upload image**, or **Image link**. Uploads accept JPG, JPEG, PNG and GIF up to 20 MiB, using the existing validated workflow-file storage. Drop a file onto the upload area or browse for one. Hosted image links must be complete HTTP(S) URLs.
5. Add up to three buttons and configure their labels and actions.
6. Use **Add card** for more cards. Drag a card by its grip, or use its up/down buttons, to change its order. The delete icon removes that card from the draft.
7. Click **Save**. Restart the tester to replay Chat started, or send the message matching your trigger.

Viewing a language with no carousel does not create an empty translation. Use **Add card** in that language to configure one. Card and button identities survive editing and reordering, while the workflow action retains its permanent step number.

## Buttons

| Button action | Fields | Behavior |
| --- | --- | --- |
| Send reply | Button label and Message sent | Adds the configured message as the customer's next chat message and runs normal Message received matching. |
| Link | Button label and Webpage URL | Opens the webpage in another tab. HTTP(S) URLs, query strings, and page anchors are supported. |
| Link with Open in webview | The same Link fields, plus the checkbox | Opens an embedded webpage inside the tester's chat panel, with Close and Open in new tab controls. |

Button labels can use up to 80 characters. Reply messages can use up to 1,000 characters. For example, two cards may both have a button labelled **Select**, while their **Message sent** values are `Transactions` and `Accounts`. Add nested Message received triggers matching those values to route the selection.

Showing a carousel continues through its children and following actions. A reply button does not directly execute an arbitrary node ID; it sends its configured message to the existing workflow matcher, just like a typed reply. Add a nested trigger where the workflow should wait. Link clicks open the webpage without posting a message, resuming a survey, or changing workflow state.

The latest available carousel reply choices remain usable when followed by ordinary explanatory text. They become inactive after the customer replies, a newer interactive prompt appears, a survey starts, or the chat closes. Link buttons remain usable as links. Failed message submissions restore the input and previous choices, consistent with the existing tester.

## Complaint category example

Create a **Message received** trigger with the exact example `make a complaint`. Under it, add **Show carousel**, named `Complaint categories`, with these cards:

| Card | Title | Subtitle | Button label | Message sent |
| --- | --- | --- | --- | --- |
| 1 | Transactions | Get help with a transaction. | Select | Transactions |
| 2 | Accounts | Get help with your account. | Select | Accounts |

Upload suitable category images if desired. Under that same carousel action, add two nested triggers:

1. **Message received: Transactions** → **Ask survey**: `What is your complaint reference?`, stored as `complaint_ref` → **Send message**: `We will check complaint {complaint_ref}.` → **Jump** to your existing main menu.
2. **Message received: Accounts** → **Send message**: `Please describe the issue with your account.` → an appropriate survey or support sequence.

Save the steps, restart the tester, send `make a complaint`, and select Transactions. You should see one carousel, then the reference question. Enter a reference and verify the confirmation and menu continuation. Swiping, the horizontal scrollbar, left/right arrow keys on the carousel region, and the previous/next controls navigate the cards.

## Localisation and variables

Configure each agent language independently using **Content language**. Titles, subtitles, button labels, reply values, images, and link choices can differ by language. Add translated trigger examples matching the translated **Message sent** values.

For example, French cards can use `Opérations` / `Comptes`, with **Choisir** buttons sending `Opérations` / `Comptes`. Configure those French examples on the corresponding triggers. **Switch Language → French** selects the French carousel on its next execution, including when reached by Jump.

The runtime uses the active language's carousel, then the agent's default, then the first configured translation. It does not translate text or images automatically. Merely viewing an unconfigured language does not affect existing content.

Title, subtitle, label, and reply-message fields support the existing `{variable}` and `{{variable}}` text substitutions, including survey `.text` and `.norm.value` views. Unknown variables, empty required rendered text, or oversized rendered text produce a controlled tester error and roll back that turn. Image and webpage URLs are fixed URLs in this release; do not put variable placeholders or Python in URL fields.

## Webview behavior

Webview embeds the chosen website in an iframe inside the tester, preserving the conversation behind it. Close returns to the chat without posting a reply. The iframe permits scripts and forms while retaining a sandboxed, separate origin and preventing access to the surrounding application or top-level navigation. The explicit **Open in new tab** link uses `noopener noreferrer`.

Some sites prohibit embedding through their response policies; others need browser storage, authentication, popups, or other features restricted by the sandbox. Those sites should be opened using **Open in new tab**. The application does not bypass a website's embedding policy, proxy its HTML, or claim it can reliably detect every blocked iframe. See the [iframe reference](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe) for browser behavior.

## Storage and compatibility

No new dependency or database migration is added. **Schema head remains 0007**, which must already be applied for image uploads. Keep the existing `.env`, database, and dependency lock files when updating the source. Older installations should run `python -m alembic upgrade head` from backend before starting the API.

Workflow JSON stores card configuration and uploaded image IDs. Uploads use PostgreSQL `workflow_assets`; sent image snapshots use `agent_attachments`, scoped to the conversation owner. A reused uploaded image is copied once per turn and shared by cards in that turn. Replacing a workflow image preserves earlier sent images. The existing 100-file/1-GiB per-agent upload quota is shared with Send Attachment. Image deletion is blocked while any saved attachment or carousel translation references the asset, including disabled steps and outcome paths. Draft/replaced image cleanup follows the existing editor behavior.

Hosted image and webpage URLs are delivered to the browser without backend fetching. HTTP(S) URLs are validated; scripts, file/data URLs, credentials, whitespace, and placeholders are rejected. External content must remain accessible. Carousel text is rendered as text rather than HTML.

Each carousel emits one assistant message regardless of card count. A turn can emit at most ten carousels, in addition to the existing workflow execution limits. Billing deductions and published-channel delivery remain outside the current tester release. Existing agent configuration exports do not package workflows or uploaded images.
