"""Short-lived read-only PostgreSQL sessions; no connection strings from clients."""
import base64
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
import json
import math
import os
import time as clock
import uuid

import psycopg
from psycopg.rows import tuple_row

from app.workflows.chat_script import ScriptError, json_value

MAX_ROWS = 50


@dataclass
class QueryResult:
    outcome: str
    rows: list = field(default_factory=list)
    columns: list = field(default_factory=list)
    truncated: bool = False
    error: object = None

    def variable(self):
        return {"ok": self.outcome == "success", "rows": self.rows, "columns": self.columns,
            "row_count": len(self.rows), "truncated": self.truncated, "error": self.error}


def cell(value):
    if value is None or type(value) in {str, bool, int}: return value
    if type(value) is float:
        if not math.isfinite(value): raise ValueError("unsupported_result")
        return value
    if isinstance(value, (datetime, date, time)): return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)): return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)): return base64.b64encode(value).decode("ascii")
    if type(value) in {list, tuple}: return [cell(item) for item in value]
    if type(value) is dict: return {str(key): cell(item) for key, item in value.items()}
    raise ValueError("unsupported_result")


class DatabaseClient:
    def connect(self, details, timeout):
        return psycopg.connect(host=details["host"], port=details["port"], dbname=details["database"],
            user=details["username"], password=details["password"], sslmode=details["ssl_mode"],
            connect_timeout=max(2, min(10, math.ceil(timeout))), application_name="Petrix AI read-only",
            passfile=os.devnull, options="-c default_transaction_read_only=on -c statement_timeout={} -c idle_in_transaction_session_timeout=10000".format(max(1, int(timeout * 1000))),
            autocommit=False, row_factory=tuple_row)

    def verify(self, details):
        try:
            with self.connect(details, 10) as connection:
                connection.read_only = True
                with connection.transaction(force_rollback=True):
                    row = connection.execute("SELECT current_setting('transaction_read_only'), 1").fetchone()
                    if row != ("on", 1): return "verification_failed"
            return None
        except (psycopg.Error, OSError, ValueError):
            # Driver errors can include hosts, usernames, SQL and passwords.
            return "connection_failed"

    def query(self, details, query, parameters, timeout):
        started = clock.monotonic()
        try:
            with self.connect(details, timeout) as connection:
                connection.read_only = True
                remaining = timeout - (clock.monotonic() - started)
                if remaining <= 0: return QueryResult("timeout", error="timeout")
                with connection.transaction(force_rollback=True):
                    connection.execute("SELECT set_config('statement_timeout', %s, true)", [str(max(1, int(remaining * 1000)))])
                    # A server cursor fetches bounded batches instead of reading
                    # an unbounded result into the Python process.
                    with connection.cursor(name="petrix_lookup") as cursor:
                        cursor.execute(query, parameters)
                        def fetch():
                            remaining = timeout - (clock.monotonic() - started)
                            if remaining <= 0: raise psycopg.errors.QueryCanceled()
                            connection.execute("SELECT set_config('statement_timeout', %s, true)", [str(max(1, int(remaining * 1000)))])
                            return cursor.fetchmany(1)
                        batch = fetch()
                        columns = [item.name for item in cursor.description]
                        if len(columns) > 50 or len(set(columns)) != len(columns):
                            return QueryResult("error", error="invalid_columns")
                        rows = []
                        truncated = False
                        while batch:
                            if clock.monotonic() - started >= timeout:
                                return QueryResult("timeout", error="timeout")
                            if len(rows) == MAX_ROWS:
                                truncated = True; break
                            row = {key: cell(value) for key, value in zip(columns, batch[0])}
                            candidate = QueryResult("success", rows + [row], columns)
                            try: json_value(candidate.variable())
                            except ScriptError:
                                if not rows: return QueryResult("error", error="result_too_large")
                                truncated = True; break
                            rows.append(row)
                            batch = fetch()
                        result = QueryResult("success", rows, columns, truncated)
                        json_value(result.variable())
                        return result
        except psycopg.errors.QueryCanceled:
            return QueryResult("timeout", error="timeout")
        except psycopg.errors.ConnectionTimeout:
            return QueryResult("timeout", error="timeout")
        except (psycopg.Error, OSError):
            return QueryResult("error", error="query_failed")
        except (ValueError, TypeError, OverflowError, RecursionError, ScriptError):
            return QueryResult("error", error="unsupported_result")
