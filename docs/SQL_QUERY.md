# SQL Query

Version 0.8.0 adds a PostgreSQL SELECT action with Success, Error, and Timeout paths. First connect and verify your database in [Database connections](DATABASE_CONNECTIONS.md).

## Settings

| Setting | Behavior |
| --- | --- |
| Database connector | A verified connection from the current workspace. Refresh the selector after verifying a connection in Settings. |
| Query | A single read-only SELECT, up to 8,000 characters in SQL template mode. |
| Query format | SQL template, or a bounded Python expression returning a static SQL string (up to 2,000 expression characters). |
| Timeout after (seconds) | 1–120 seconds, default 30. Connection setup uses at most 10 seconds of the lookup budget; PostgreSQL statement timeouts and remaining-budget checks govern execution/fetching. |
| Resulting variable | A custom chat variable, starting with an ASCII letter, then letters/numbers/underscores, up to 64 characters. Reserved/system names are rejected. |

Use **Save**, then add subsequent steps under the three paths in the workflow tree. Path containers have no step number; actions inside them keep permanent numbers and can be Jump destinations. After the chosen path finishes, following sibling actions continue. A survey or nested trigger inside a path can pause and later resume normally.

## Safe chat parameters

In SQL template mode:

```sql
SELECT ref, status FROM public.complaints WHERE ref = {complaint_ref}
```

`{complaint_ref}`, `{{complaint_ref}}`, and quoted `'{{complaint_ref}}'` are all bound as a single value. Text containing quotes, SQL keywords, or semicolons remains data. Do not concatenate or interpolate raw answers into SQL. Placeholders refer to values, not table names, column names, or SQL fragments.

Survey text also supports `{complaint_ref.text}`; a numeric survey answer supports `{account_id.norm.value}`. Plain variable names and scalar object fields work too. Missing variables or non-scalar parameter values follow Error without contacting the database. Null, text, numeric, and Boolean parameters preserve their types.

In Python expression mode, return the SQL **template** as a string:

```python
"SELECT ref, status FROM public.complaints WHERE ref = {complaint_ref}"
```

Expressions use the existing bounded Python chat-script evaluator. The SQL template expression has no access to chat variables; use the bound placeholders inside its string. Direct interpolation such as `"SELECT ... '" + complaint_ref + "'"` is rejected. This avoids turning a person's reply into SQL syntax.

Supported SQL includes joins, scalar expressions, aggregates, UNIONs, and SELECT subqueries. Writes, multiple statements, WITH statements, SELECT INTO, locking clauses, session changes, sequence modifications, and known side-effect primitives are rejected. A trailing semicolon and SQL comments are accepted. String/identifier escapes with backslashes are excluded to avoid ambiguous parsing. PostgreSQL also enforces read-only access while executing the query. Use a dedicated SELECT-only database account and restrict custom function/extension privileges.

## Result structure

For resulting variable `complaint_status`:

```json
{
  "ok": true,
  "rows": [{"ref": "REF-123", "status": "resolved"}],
  "columns": ["ref", "status"],
  "row_count": 1,
  "truncated": false,
  "error": null
}
```

Always check `complaint_status.row_count > 0` before reading the first row. An empty SELECT is successful (`rows: []`, `row_count: 0`), not an Error. A Send message displays one field with `{complaint_status.rows.0.status}`; using `{complaint_status}` prints the full result object.

At most 50 rows and 50 distinct column names are returned. Use aliases for duplicate column names. Results also obey existing chat-variable limits: 16 KiB per value, up to 8 levels of nesting, and 64 KiB/100 variables per chat. `truncated: true` means the row or size limit stopped collection; use SQL filters/order/limit for precise lookups. `row_count` counts returned rows, not all matches. An individual row that cannot fit follows Error. Decimal amounts are strings to preserve exactness; dates/times and UUIDs become strings, bytea becomes base64, and supported JSON/arrays stay structured.

Error and Timeout store the same shape with `ok: false`, empty rows/columns, row_count 0, and a controlled error code. Query/connection details and credentials are not included. Codes include `invalid_query`, `connection_unavailable`, `credentials_unavailable`, `query_failed`, `invalid_columns`, `result_too_large`, `unsupported_result`, `variable_limit`, `query_limit`, and `timeout`. A turn supports at most 10 database lookups. Unverified/deleted/foreign connectors cannot execute. Query errors choose Error; query cancellation or connection timeout chooses Timeout. TLS errors choose Error. Verification establishes access at that time; each runtime lookup checks status and opens a fresh connection.

## Complete complaint flow

Create `public.complaints` and sample data in your own database outside Petrix, then grant your reader SELECT access. Assume REF-123 has status `resolved`.

1. **Message received:** example `Check complaint`.
2. **Ask survey:** “What is your complaint reference?”, Text, variable `complaint_ref`, skipping disabled.
3. **SQL Query:** verified `complaints_db`, SQL template `SELECT ref, status FROM public.complaints WHERE ref = {complaint_ref}`, timeout 30, variable `complaint_status`.
4. Under **Success**, add **Branch**:
   - If `complaint_status.row_count > 0 and complaint_status.rows[0].status == "resolved"`: Send message `Good news – complaint {complaint_status.rows.0.ref} is resolved.`
   - Else if `complaint_status.row_count > 0`: Send message `Complaint {complaint_status.rows.0.ref} is currently {complaint_status.rows.0.status}.`
   - Else: Send message `I could not find that complaint reference.`
5. Under **Error**: Send message `I could not check your complaint right now. Please try again.`
6. Under **Timeout**: Send message `The lookup took too long. Please try again shortly.`
7. After SQL Query, add **Jump** to your existing Main menu step.

Restart the tester, send `Check complaint`, and answer `REF-123`. Expect the resolved message followed by the Main menu. Test an unknown reference for the empty-result path. Test query failure using a missing table in a dedicated development database. Do not modify a live connector just to simulate errors. The tester variable inspector shows rows; its trace shows the selected outcome and returned row count.

## Execution guarantees and deployment checks

Each lookup uses explicit psycopg keyword connection parameters, parameter binding, a short-lived read-only transaction with forced rollback, server-side cursor fetching, controlled TLS mode, PostgreSQL statement timeouts, and bounded results. No SQL is executed by an OS shell or Python eval/exec. Saving the workflow validates connector ownership/verification and path integrity. CSRF, workspace membership, Manage AI Agents, workflow revisions, and tester conversation ownership continue to apply.

Run the backend test suite against an isolated PostgreSQL test database, never a production database. The tests create/drop temporary schemas and test tables. Ordinary PostgreSQL remains the runtime dependency; the local QA harness used for this deliverable is not shipped as a database requirement.

PostgreSQL read-only behavior and psycopg parameter handling are described in the official [SET TRANSACTION](https://www.postgresql.org/docs/current/sql-set-transaction.html), [transaction management](https://www.psycopg.org/psycopg3/docs/basic/transactions.html), and [query parameter](https://www.psycopg.org/psycopg3/docs/basic/params.html) documentation.
