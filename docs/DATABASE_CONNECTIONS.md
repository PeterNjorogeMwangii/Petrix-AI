# Database connections

Version 0.8.0 adds Settings → Teamspace → Database connections and the SQL Query action. The working connector in this release is **PostgreSQL**. Other Settings sections are present as planned pages; existing Roles & permissions remains functional.

## Upgrade

Keep your existing `backend/.env`, database, and virtual environment. From the backend directory:

```powershell
python -m pip install -r requirements.lock.txt
python -m alembic upgrade head
python -m alembic current
```

The schema should report `0006 (head)`. This migration creates `database_connections`; it does not rewrite existing workflows, step numbers, agent settings, accounts, or chat history. Restart the API and Angular using your existing commands. No Docker setup is required. cryptography, already pinned in the lock, is now a direct dependency for credential encryption; other locked versions are unchanged.

## Connect PostgreSQL

1. Open Settings from the main sidebar or profile menu.
2. Select **Teamspace → Database connections → Connect database**.
3. Enter a unique connection name, host, port, database name, username, and password. Use the hostname reachable **from the backend**, not from the browser. The default port is 5432.
4. Choose TLS mode. **Verify certificate and hostname** is the default and uses the trusted PostgreSQL root CA configured on the backend host (`~/.postgresql/root.crt`, or `%APPDATA%\postgresql\root.crt` on Windows). **Require encryption** encrypts traffic without full certificate/hostname verification. **Disabled** is available for local development.
5. Click **Connect and verify**. Petrix checks a real read-only transaction before making the connection available to workflows.

The table shows Name, Type, and Status. A failed verification remains visible with **Verification failed** so you can correct its details. Use **Edit** to change details, **Verify** to retry, and **Delete** to remove an unused connection. Editing always verifies the new settings. An empty password while editing keeps the saved password. Failed verification clears its verified status, so it disappears from the action selector and existing lookups follow Error until it is fixed.

Owners and administrators manage connections. People with Manage AI Agents permission can select verified connections in their own workspace; the options endpoint supplies only IDs, names, and types. Host details and credentials are not part of workflow documents. Up to 20 connections are supported per workspace. Names are unique without regard to case. Stale edits/deletions return 409 instead of replacing newer changes. Remove or change SQL Query actions before deleting a connection they reference.

## Read-only database account

Use a dedicated, non-administrative database account with SELECT access only to the tables or views the agent needs. Do not connect using a superuser or a role with privileged user-defined functions, extensions, filesystem access, or external database access. Petrix executes each query in a server-enforced read-only transaction and always rolls it back, validates a single SELECT, binds chat data as parameters, rejects locking/session-changing forms, and closes the connection after the operation. Custom functions still run with the privileges granted by your database; database grants remain the boundary for which data a connector may access.

For example, an administrator can provision a reader (substitute your database, schema, and secret; do this outside Petrix):

```sql
CREATE ROLE petrix_reader LOGIN PASSWORD 'replace-with-a-strong-secret';
GRANT CONNECT ON DATABASE support TO petrix_reader;
GRANT USAGE ON SCHEMA public TO petrix_reader;
GRANT SELECT ON public.complaints TO petrix_reader;
ALTER ROLE petrix_reader SET default_transaction_read_only = on;
```

Grant access deliberately for each additional table/view. This example does not grant write permissions or access to every future table.

## Credential encryption

Passwords are encrypted at rest using Fernet and are never returned in API responses, options, workflow variables, or execution traces. Driver error strings are replaced by controlled error codes. Keep application database backups and the encryption key secure.

By default, the encryption key is derived from `SESSION_SIGNING_SECRET` using a separate purpose-specific derivation. Keep that secret stable or existing connector passwords cannot be decrypted. You can instead set a dedicated `DATABASE_CONNECTIONS_ENCRYPTION_KEY` in `backend/.env` **before saving your first connection**:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store that output as the environment value and restart the API. Back it up separately. Changing either the dedicated key or the fallback session secret requires re-entering existing connector passwords; this release does not automatically re-encrypt old credentials. A missing/wrong key produces a controlled credentials-unavailable error. Credentials are entered only in the Settings form; never place them in queries or workflow variables.

See [SQL_QUERY.md](SQL_QUERY.md) for configuration and a complete survey → database → Branch → Send message flow.
