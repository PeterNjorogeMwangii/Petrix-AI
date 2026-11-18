import argparse,time,threading,logging
from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.core.security import utcnow
from app.models import TeamspaceOperations,SupportTicket,TicketNote,User,SupportTeam,ChatAssignment
from app.teamspace import jobs,records as r


def deadline(start,days,exclude_weekends):
 local=start.astimezone(ZoneInfo('Africa/Nairobi'));remaining=days
 while remaining:
  local+=timedelta(days=1)
  if not exclude_weekends or local.weekday()<5:remaining-=1
 return local

def overdue(db,settings):
 now=utcnow()
 for ops in db.scalars(select(TeamspaceOperations)):
  config=ops.overdue
  if not config.get('enabled'):continue
  tickets=db.scalars(select(SupportTicket).where(SupportTicket.workspace_id==ops.workspace_id,SupportTicket.status.in_(['open','in_progress']),SupportTicket.updated_at<=now-timedelta(days=config.get('days',15))).order_by(SupportTicket.updated_at).limit(500).with_for_update(skip_locked=True)).all()
  for ticket in tickets:
   if r.record_space(db,'tickets',ticket)!=ops.teamspace_id or now<deadline(ticket.updated_at,config.get('days',15),config.get('exclude_weekends',False)):continue
   teams=list(db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==ops.workspace_id,SupportTeam.archived.is_(False),SupportTeam.id.in_([__import__('uuid').UUID(i) for i in config.get('team_ids',[])]))))
   user=db.get(User,__import__('uuid').UUID(config['user_id'])) if config.get('user_id') else None
   from app.workspace_settings.permissions import membership
   if user and not membership(db,ops.workspace_id,user.id):user=None
   if user:ticket.assignee_type='user';ticket.assignee_id=user.id;ticket.assignee_name=user.full_name or user.email
   elif teams:ticket.assignee_type='team';ticket.assignee_id=teams[0].id;ticket.assignee_name=', '.join(t.name for t in teams)
   ticket.audience_team_ids=[str(t.id) for t in teams];ticket.status='overdue';ticket.overdue_at=now;ticket.revision+=1
   db.add(TicketNote(ticket_id=ticket.id,author_name='Petrix AI',kind='event',content='Ticket marked overdue after the configured inactivity period.'))
 db.commit()

def tick(factory,settings,ai,mailer):
 with factory() as db:
  overdue(db,settings);ids=list(db.scalars(select(ChatAssignment.workspace_id)))
 for identifier in ids:
  with factory() as db:
   from app.teamspace.assignment import drain
   drain(db,identifier)
 jobs.run_one(factory,settings,ai,mailer)

def start(application):
 stop=threading.Event()
 def loop():
  while not stop.is_set():
   try:tick(application.state.session_factory,application.state.settings,application.state.support_ai,application.state.mailer)
   except Exception as error:logging.getLogger(__name__).error('Support worker tick failed (%s). Durable work will retry.',type(error).__name__)
   stop.wait(max(1,application.state.settings.support_worker_interval))
 thread=threading.Thread(target=loop,name='petrix-support-worker',daemon=True);thread.start();return stop,thread

def main():
 from app.core.config import Settings
 from app.database import create_database
 from app.mailer import SMTPMailer
 from app.teamspace.ai import SupportAI
 parser=argparse.ArgumentParser();parser.add_argument('--watch',action='store_true');args=parser.parse_args();settings=Settings();engine,factory=create_database(settings.database_url.get_secret_value())
 try:
  while True:
   tick(factory,settings,SupportAI(settings),SMTPMailer(settings))
   if not args.watch:break
   time.sleep(max(1,settings.support_worker_interval))
 finally:engine.dispose()
if __name__=='__main__':main()
