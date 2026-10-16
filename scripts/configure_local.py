"""Configure local PostgreSQL from backend/.env. Compatible with Python 3.9.12."""
import secrets
from codecs import BOM_UTF8, BOM_UTF16_LE, BOM_UTF16_BE
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def merge_configuration(content, updates):
    lines = []
    written = set()
    for line in content.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                if key not in written:
                    lines.append("{}={}".format(key, updates[key]))
                    written.add(key)
                continue
        lines.append(line)
    for key, value in updates.items():
        if key not in written:
            lines.append("{}={}".format(key, value))
    return "\n".join(lines) + "\n"


def main():
    try:
        import psycopg
        from dotenv import dotenv_values
        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import ArgumentError
    except ImportError:
        print("Install backend dependencies first: python -m pip install -r backend/requirements.lock.txt")
        return 1
    target = ROOT / "backend" / ".env"
    if not target.is_file():
        print("Create backend/.env first: Copy-Item .\\backend\\.env.example .\\backend\\.env")
        print("Set DATABASE_URL in backend/.env to your local PostgreSQL connection.")
        return 1
    print("Reading configuration: {}".format(target))
    try:
        raw = target.read_bytes()
        # Windows editors and PowerShell can prepend a byte-order mark.
        encoding = "utf-16" if raw.startswith((BOM_UTF16_LE, BOM_UTF16_BE)) else "utf-8-sig"
        content = raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeError):
        print("Could not read backend/.env. Check that it is accessible and saved as UTF-8 or UTF-16 with a byte-order mark.")
        return 1
    existing = dotenv_values(stream=StringIO(content))
    database_url = (existing.get("DATABASE_URL") or "").strip()
    if not database_url:
        print("DATABASE_URL is missing or empty in the file shown above.")
        print("Edit backend/.env and save your PostgreSQL connection as DATABASE_URL=...")
        return 1
    try:
        url = make_url(database_url)
        if (
            url.drivername != "postgresql+psycopg"
            or not url.host
            or not url.username
            or not url.database
            or (url.port is not None and not 1 <= url.port <= 65535)
        ):
            raise ValueError
        normalized_url = url.render_as_string(hide_password=False)
        connection_url = url.set(drivername="postgresql").render_as_string(hide_password=False)
    except (ArgumentError, ValueError):
        print("Set a valid DATABASE_URL in backend/.env using this format:")
        print("postgresql+psycopg://USERNAME:PASSWORD@localhost:5432/petrix")
        return 1
    print("Checking the PostgreSQL connection from backend/.env...")
    try:
        with psycopg.connect(connection_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise psycopg.OperationalError("Connection check failed")
    except psycopg.Error:
        print("Could not connect. Check the PostgreSQL service and DATABASE_URL in backend/.env, then retry.")
        return 1
    secret = existing.get("SESSION_SIGNING_SECRET") or ""
    updates = {}
    if normalized_url != database_url:
        updates["DATABASE_URL"] = normalized_url
    if len(secret) < 32:
        updates["SESSION_SIGNING_SECRET"] = secrets.token_hex(32)
    normalize_encoding = raw.startswith((BOM_UTF8, BOM_UTF16_LE, BOM_UTF16_BE))
    if updates or normalize_encoding:
        try:
            target.write_text(
                merge_configuration(content, updates) if updates else content,
                encoding="utf-8",
            )
        except OSError:
            print("Database connection verified, but could not save backend/.env. Check write permissions.")
            return 1
    if "DATABASE_URL" in updates:
        print("Saved DATABASE_URL with correctly encoded credentials.")
    if "SESSION_SIGNING_SECRET" in updates:
        print("Generated and saved SESSION_SIGNING_SECRET in backend/.env.")
    if normalize_encoding:
        print("Saved backend/.env as UTF-8 without a byte-order mark for the backend.")
    print("Database connection verified.")
    print("Next: cd backend")
    print("Then: python -m alembic upgrade head")
    print("Then: python -m uvicorn app.main:create_app --factory --reload --port 8000 --no-access-log")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nConfiguration cancelled.")
        raise SystemExit(1)
