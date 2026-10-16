# Authentication and account association

## Independent registration

An invitation is not needed. All syntactically valid, eligible personal and professional email addresses are accepted, including Gmail and Yahoo. `BLOCKED_EMAIL_DOMAINS` optionally contains a comma-separated domain block list. No corporate-domain-only policy is applied.

Email is normalized using a case-insensitive account policy. Gmail dots and plus aliases are not rewritten. Email format validation does not require an MX lookup at registration; ownership is proved through the inbox link.

The user, initial workspace, and Owner membership are created in one database transaction after authentication succeeds. The workspace starts with a Default AI Agent, its first configuration version, and default role permissions in that same transaction. Unique email constraints and PostgreSQL conflict handling prevent concurrent registration attempts for one normalized email from creating duplicate users and workspaces.

On later sign-ins, the same user and existing membership are loaded. An unfinished setup returns to onboarding. A completed setup returns to `/app/agents`.

## Magic links

Links contain 32 random bytes encoded as a URL-safe token. Only its SHA-256 digest is stored in the magic-link table. The configured lifetime is `MAGIC_LINK_MINUTES`, initially 15. Consumption is a conditional database UPDATE requiring an unused, unexpired token. GET requests never consume tokens. Account creation and session issuance commit only after successful validation.

The email-request endpoint is limited per normalized email and client IP in a database-backed fixed time window. Initial limits are five requests per email and thirty per IP in fifteen minutes. The response does not disclose whether the account already exists. Invalid or explicitly blocked addresses can be rejected according to the registration policy.

SMTP acceptance is required before returning 202. On SMTP failure, the token and pending changes are rolled back and the client receives 503. Application logs omit recipients, authentication links, and raw provider token responses. A delivered message could become unusable if a subsequent database commit fails; the user can request another link.

## Google

Create a Web application OAuth client for Petrix AI in your Google project. Configure the local callback exactly as:

`http://localhost:8000/api/auth/oauth/google/callback`

Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` on the backend. Google sign-in requests `openid email profile` and uses PKCE. Authlib validates OAuth state and the ID token's signature, issuer, audience, expiry, and nonce.

For a verified `@gmail.com` address, or a verified Google Workspace address with an `hd` claim, Google is treated as authoritative for the mailbox. A matching existing normalized email is associated with that verified identity, avoiding a second user or workspace. Otherwise Petrix AI requests fresh mailbox verification before first association. The provider's email address alone does not grant access.

## Microsoft

Register a Web application in Microsoft Entra. To accept personal and work accounts, select the audience that supports accounts in any organization directory and personal Microsoft accounts. Use the local callback:

`http://localhost:8000/api/auth/oauth/microsoft/callback`

Set `MICROSOFT_CLIENT_ID` and `MICROSOFT_CLIENT_SECRET`. The default authority is `common`; the implementation also accepts `consumers`, `organizations`, or a configured tenant UUID. Use an authorization code flow; the application does not use the implicit grant flow.

Authlib validates signature, audience, expiry, nonce, state, and PKCE. For the `common` issuer template, the code additionally validates that the signed tenant ID is a UUID and that the issuer exactly equals `https://login.microsoftonline.com/{tid}/v2.0`. Configured tenant restrictions are enforced.

A Microsoft email claim is treated as a hint, not proof of ownership. On first association, the user confirms an email through a fresh magic link. An existing Petrix user should enter the email already associated with their account. Completing the link associates the stable Microsoft issuer and subject with that user. Later Microsoft sign-ins use the linked issuer and subject directly.

An external identity cannot be reassigned to a different account through this flow. Database uniqueness enforces that rule.

## Sessions and workspace access

Sessions are random opaque tokens whose hashes are stored in PostgreSQL, initially lasting seven days. Session tokens are not stored in browser local storage. Sign-out revokes the current session. Authenticated mutations require a session-bound CSRF token and an allowed browser origin. Workspace metadata, setup, and avatars require membership; initial setup additionally requires Owner permission.

Country choices come from ISO country data. `SUPPORTED_COUNTRIES` optionally restricts that list. `DEFAULT_COUNTRY=KE` is a configurable initial selection, not geolocation detection; users can change it. Names and countries are required. Industry defaults to Other. Website and avatar can be omitted.

## Environment boundary

Development defaults use Mailpit. Enabling an OAuth button requires both its backend client ID and secret. Live Google/Microsoft provider authentication needs valid app registrations and matching callback URLs; tests use local identity data and do not log in to real provider accounts.

Production requires HTTPS origins, Secure cookies, an adequately long signing secret, and encrypted SMTP. Serve the frontend and API behind one public origin so session/CSRF cookies work consistently. Configure trusted ingress addresses before relying on forwarded client IPs for throttling.
