from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.security import utcnow
from app.database_connections.client import QueryResult
from app.database_connections.credentials import decrypt, encrypt
from app.models import Agent, DatabaseConnection, WorkflowDocument
from app.workflows.numbering import walk


def payload(connection):
    return {key: getattr(connection, key) for key in ("id", "name", "type", "host", "port", "database", "username",
        "ssl_mode", "status", "last_error", "revision", "verified_at", "created_at", "updated_at")}


def details(connection, settings):
    return {**{key: getattr(connection, key) for key in ("host", "port", "database", "username", "ssl_mode")},
        "password": decrypt(connection.password_encrypted, settings)}


def get_connection(db, workspace_id, connection_id, lock=False):
    query = select(DatabaseConnection).where(DatabaseConnection.id == connection_id, DatabaseConnection.workspace_id == workspace_id)
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    connection = db.scalar(query)
    if connection is None: raise HTTPException(404, "Database connection not found.")
    return connection


def check_revision(connection, expected):
    if connection.revision != expected:
        raise HTTPException(409, "This connection changed in another session. Reload before saving.")


def check_name(db, workspace_id, name, current_id=None):
    duplicate = db.scalar(select(DatabaseConnection.id).where(DatabaseConnection.workspace_id == workspace_id,
        func.lower(DatabaseConnection.name) == name.lower(), DatabaseConnection.id != current_id))
    if duplicate: raise HTTPException(409, "A database connection with this name already exists.")


def verify(connection, client, settings):
    try: error = client.verify(details(connection, settings))
    except ValueError: error = "credentials_unavailable"
    connection.status = "verified" if error is None else "error"
    connection.last_error = error
    connection.verified_at = utcnow() if error is None else None
    connection.updated_at = utcnow()


def update_fields(connection, body, settings):
    for key, value in body.model_dump(exclude={"password", "expected_revision"}).items():
        setattr(connection, key, value)
    password = body.password.get_secret_value() if body.password else ""
    if password: connection.password_encrypted = encrypt(password, settings)


def references(db, workspace_id, connection_id):
    documents = db.execute(select(Agent.name, WorkflowDocument.nodes).join(WorkflowDocument, WorkflowDocument.agent_id == Agent.id)
        .where(Agent.workspace_id == workspace_id)).all()
    return [name for name, nodes in documents if any(node["type"] == "sql_query" and
        node["config"].get("connection_id") == str(connection_id) for node in walk(nodes))]


def validate_references(db, workspace_id, nodes):
    ids = {node["config"]["connection_id"] for node in walk(nodes) if node["kind"] == "action" and node["type"] == "sql_query"}
    if not ids: return
    import uuid
    connections = db.scalars(select(DatabaseConnection).where(DatabaseConnection.workspace_id == workspace_id,
        DatabaseConnection.id.in_([uuid.UUID(value) for value in ids]))).all()
    if {str(item.id) for item in connections if item.status == "verified"} != ids:
        raise HTTPException(422, "Choose a verified database connection from this workspace for every SQL Query action.")


class SQLQueryService:
    def __init__(self, db, workspace_id, settings, client):
        self.db, self.workspace_id, self.settings, self.client = db, workspace_id, settings, client

    def query(self, connection_id, query, parameters, timeout):
        import uuid
        # The shared lock prevents a concurrent edit/delete from changing the
        # connector between authorization and completion of the lookup.
        connection = self.db.scalar(select(DatabaseConnection).where(DatabaseConnection.id == uuid.UUID(connection_id),
            DatabaseConnection.workspace_id == self.workspace_id).with_for_update(read=True).execution_options(populate_existing=True))
        if connection is None or connection.status != "verified":
            return QueryResult("error", error="connection_unavailable")
        try: credentials = details(connection, self.settings)
        except ValueError: return QueryResult("error", error="credentials_unavailable")
        return self.client.query(credentials, query, parameters, timeout)
