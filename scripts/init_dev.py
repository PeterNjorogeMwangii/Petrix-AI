"""Create local configuration once; never overwrite existing secrets."""
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_env(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def main():
    root_env = ROOT / ".env"
    if not root_env.exists():
        root_env.write_text(
            f"POSTGRES_PASSWORD={secrets.token_hex(24)}\nSESSION_SIGNING_SECRET={secrets.token_hex(32)}\n"
            "GOOGLE_CLIENT_ID=\nGOOGLE_CLIENT_SECRET=\nMICROSOFT_CLIENT_ID=\nMICROSOFT_CLIENT_SECRET=\n",
            encoding="utf-8",
        )
        print("Created local configuration.")
    else:
        print("Keeping existing local configuration.")
    values = parse_env(root_env)
    if not values.get("POSTGRES_PASSWORD") or len(values.get("SESSION_SIGNING_SECRET", "")) < 32:
        raise SystemExit("Fill POSTGRES_PASSWORD and a 32+ character SESSION_SIGNING_SECRET in .env.")
    backend_env = ROOT / "backend" / ".env"
    if not backend_env.exists():
        template = (ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
        template = template.replace("postgres:REPLACE_WITH_LOCAL_DATABASE_PASSWORD@localhost:5432", "petrix:REPLACE_WITH_LOCAL_DATABASE_PASSWORD@localhost:5434")
        template = template.replace("REPLACE_WITH_LOCAL_DATABASE_PASSWORD", values["POSTGRES_PASSWORD"])
        template = template.replace("SESSION_SIGNING_SECRET=\n", f"SESSION_SIGNING_SECRET={values['SESSION_SIGNING_SECRET']}\n")
        backend_env.write_text(template, encoding="utf-8")
        print("Created configuration for local backend development.")
    print("Ready. Run: docker compose up --build")


if __name__ == "__main__":
    main()
