"""Run `python -m app.workspace_settings.worker` periodically for saved auto top-ups."""
from sqlalchemy import select
from app.core.config import Settings
from app.database import create_database
from app.models import WorkspaceBilling
from app.workspace_settings.billing import StripeClient,run_topup

def main():
 settings=Settings();engine,factory=create_database(settings.database_url.get_secret_value());stripe=StripeClient(settings)
 with factory() as db:ids=list(db.scalars(select(WorkspaceBilling.workspace_id).where(WorkspaceBilling.auto_topup['enabled'].as_boolean().is_(True))))
 for workspace_id in ids:
  with factory() as db:
   try:print(str(workspace_id),run_topup(db,workspace_id,stripe,settings))
   except Exception:db.rollback();print(str(workspace_id),'needs_attention')
 engine.dispose()
if __name__=='__main__':main()
