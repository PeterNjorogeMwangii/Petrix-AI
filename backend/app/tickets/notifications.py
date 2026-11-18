"""Commit ticket and mail intent before SMTP; never automatically replay a send."""
from datetime import timedelta
from sqlalchemy import select
from app.core.security import utcnow
from app.models import TicketEmail,SupportTicket,TicketEmailDomain
from app.tickets.settings import valid_sender
from app.workflows.chat_script import ScriptError


class DeliveryError(Exception):
    def __init__(self,uncertain=False):self.uncertain=uncertain


def dispatch(db,mailer,conversation_id=None,ticket_id=None,limit=10,email_id=None):
    count=0
    while count<limit:
        query=select(TicketEmail).join(SupportTicket,SupportTicket.id==TicketEmail.ticket_id).where(TicketEmail.status=='queued').order_by(TicketEmail.created_at,TicketEmail.id)
        if conversation_id:query=query.where(SupportTicket.conversation_id==conversation_id)
        if ticket_id:query=query.where(SupportTicket.id==ticket_id)
        if email_id:query=query.where(TicketEmail.id==email_id)
        row=db.scalar(query.with_for_update(of=TicketEmail,skip_locked=True).limit(1).execution_options(populate_existing=True))
        if not row:db.commit();break
        ticket=db.get(SupportTicket,row.ticket_id);domain=db.get(TicketEmailDomain,row.domain_id) if row.domain_id else None
        authorized=valid_sender(domain)
        if row.kind=='reply':
            from app.teamspace import records
            from app.models import SupportPerson
            from app.workspace_settings.permissions import membership
            try:
                if not row.actor_id or not membership(db,ticket.workspace_id,row.actor_id):raise ValueError()
                records.record(db,ticket.workspace_id,'tickets',ticket.id,row.actor_id,'manage')
                if ticket.person_id and records.is_banned(db.get(SupportPerson,ticket.person_id)):raise ValueError()
            except Exception:authorized=False
        if row.recipient_type=='assignee':
            from app.tickets.service import assignee
            try:authorized=authorized and row.recipient in assignee(db,ticket.workspace_id,ticket.assignee_type,ticket.assignee_id)[1]
            except ScriptError:authorized=False
        if not authorized:
            row.status='failed';row.error='The sender or assignee is no longer eligible. Review ticket and email settings.';row.revision+=1;row.updated_at=utcnow();db.commit();count+=1;continue
        row.status='sending';row.attempts+=1;row.revision+=1;row.updated_at=utcnow();db.commit()
        try:
            mailer.send_ticket(row)
            row.status='sent';row.sent_at=utcnow();row.error=''
        except DeliveryError as error:
            row.status='uncertain' if error.uncertain else 'failed'
            row.error='Delivery was not confirmed. Check the mail server before retrying.' if error.uncertain else 'The SMTP server could not send this email. Check delivery configuration.'
        except Exception:
            row.status='uncertain';row.error='Delivery was not confirmed. Check the mail server before retrying.'
        row.revision+=1;row.updated_at=utcnow();db.commit();count+=1
    return count


def email_payload(row):
    status='uncertain' if row.status=='sending' and row.updated_at<utcnow()-timedelta(minutes=2) else row.status
    return {'id':str(row.id),'kind':row.kind,'persona':row.persona if row.kind=='reply' else {},'body_text':row.body_text if row.kind=='reply' else '', 'recipient_type':row.recipient_type,'recipient':row.recipient,'sender':row.sender,'subject':row.subject,'status':status,'attempts':row.attempts,'error':row.error,
        'revision':row.revision,'created_at':row.created_at,'sent_at':row.sent_at}
