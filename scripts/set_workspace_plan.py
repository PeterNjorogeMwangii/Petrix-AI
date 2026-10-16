"""Update workspace capacity as a platform administrator. Python 3.9 compatible."""
import argparse
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser(description='Set an existing workspace plan and capacity.')
    parser.add_argument('workspace_id', type=uuid.UUID)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--agents', required=True, type=int)
    parser.add_argument('--languages', required=True, type=int)
    args = parser.parse_args()
    name = args.plan.strip()
    if not 1 <= len(name) <= 60 or args.agents < 1 or not 1 <= args.languages <= 20:
        parser.error('Use a plan name of 1–60 characters, a positive agent limit, and 1–20 languages.')
    from sqlalchemy import func, select
    from sqlalchemy.exc import SQLAlchemyError
    from app.core.config import Settings
    from app.database import create_database
    from app.models import Agent, Workspace
    try:
        settings = Settings(_env_file=ROOT / 'backend' / '.env')
        engine, factory = create_database(settings.database_url.get_secret_value())
        try:
            with factory() as db:
                workspace = db.scalar(select(Workspace).where(Workspace.id == args.workspace_id).with_for_update())
                if not workspace:
                    print('Workspace not found.'); return 1
                count = db.scalar(select(func.count()).select_from(Agent).where(Agent.workspace_id == workspace.id))
                languages = db.scalar(select(func.max(func.json_array_length(Agent.languages))).where(Agent.workspace_id == workspace.id)) or 0
                if count > args.agents or languages > args.languages:
                    print('The proposed limits are below current usage. Reduce usage before lowering capacity.'); return 1
                workspace.plan_name, workspace.agent_limit, workspace.language_limit = name, args.agents, args.languages
                db.commit()
            print('Workspace plan and capacity updated. Reload the dashboard to see the change.')
        finally:
            engine.dispose()
    except (ValueError, SQLAlchemyError):
        print('Could not update the plan. Check backend/.env and apply the current Alembic migration first.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
