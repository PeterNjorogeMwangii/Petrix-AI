"""Evaluate published-channel inactivity independently of an open browser."""
import argparse,threading,time,uuid
from datetime import timedelta
from fastapi import Request,HTTPException
from sqlalchemy import select,func,text
from app.core.security import utcnow
from app.models import AgentConversation,AgentMessage,WebchatChannel as Channel,WebchatConversation as Link,WebchatSession as Visitor
from app.webchat import service as s
from app.workflows.schemas import TesterEvent

def expire(db,visitor,row,record,request,config):
    db.scalar(select(Visitor).where(Visitor.id==visitor.id).with_for_update())
    chat=db.get(AgentConversation,record.conversation_id,populate_existing=True)
    if not chat or chat.workflow_state.get('closed') or not config['auto_close']:return False
    last=db.scalar(select(func.max(AgentMessage.created_at)).where(AgentMessage.conversation_id==chat.id)) or chat.created_at
    if utcnow()-last<timedelta(minutes=config['timeout_minutes']):return False
    key=uuid.uuid5(chat.id,'webchat-auto-timeout:'+last.isoformat())
    receipt,duplicate=s.receipt(db,visitor,key,{'timeout':str(chat.id)},chat.id)
    if duplicate:return False
    chat=db.scalar(select(AgentConversation).where(AgentConversation.id==chat.id).with_for_update().execution_options(populate_existing=True))
    newest=db.scalar(select(func.max(AgentMessage.created_at)).where(AgentMessage.conversation_id==chat.id)) or chat.created_at
    if chat.workflow_state.get('closed') or newest!=last:
        receipt.status='completed';db.commit();return False
    s.execute(db,visitor,row,record,TesterEvent(event='chat_timeout',conversation_id=chat.id,client_event_id=key),request)
    return True

def run_one(application):
    last=select(func.max(AgentMessage.created_at)).where(AgentMessage.conversation_id==AgentConversation.id).correlate(AgentConversation).scalar_subquery()
    cutoff=utcnow()-Channel.config['timeout_minutes'].as_integer()*text("INTERVAL '1 minute'")
    query=select(Link,Visitor,Channel).join(Visitor,Link.session_id==Visitor.id).join(Channel,Visitor.channel_id==Channel.id).join(AgentConversation,Link.conversation_id==AgentConversation.id).where(
        Channel.enabled.is_(True),Channel.config['auto_close'].as_boolean().is_(True),Visitor.preview_hash=='',
        AgentConversation.workflow_state['closed'].as_boolean().is_not(True),func.coalesce(last,AgentConversation.created_at)<=cutoff).order_by(Link.created_at).limit(20)
    with application.state.session_factory() as db:
        candidates=list(db.execute(query))
        for record,visitor,channel in candidates:
            request=Request({'type':'http','app':application,'method':'POST','path':'/api/webchat/'+str(channel.id)+'/timeout','query_string':b'','scheme':'http','server':('localhost',8000),'client':('127.0.0.1',0),'headers':[]})
            try:
                if expire(db,visitor,channel,record,request,channel.config):return True
            except HTTPException:
                db.rollback()
                # Durable receipts prevent replay of an interrupted external action.
                continue
        db.rollback();return False

def start(application):
    stop=threading.Event()
    def loop():
        import logging
        while not stop.is_set():
            try:run_one(application)
            except Exception:logging.getLogger(__name__).error('Webchat timeout worker tick failed; receipts remain available for review.')
            stop.wait(5)
    thread=threading.Thread(target=loop,name='petrix-webchat-timeouts',daemon=True);thread.start();return stop,thread

def main():
    from app.main import create_app
    parser=argparse.ArgumentParser();parser.add_argument('--watch',action='store_true');args=parser.parse_args();application=create_app()
    try:
        while True:
            run_one(application)
            if not args.watch:break
            time.sleep(5)
    finally:application.state.engine.dispose()
if __name__=='__main__':main()
