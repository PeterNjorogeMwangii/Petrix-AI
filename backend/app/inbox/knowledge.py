"""Bounded, same-origin Copilot website ingestion, separate from agent Knowledge."""
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urldefrag
import time
import uuid
from fastapi import HTTPException
from sqlalchemy import select
from app.models import CopilotKnowledge, SupportJob
from app.core.security import utcnow
from app.agent_knowledge.web import WebsiteReader
from app.agent_knowledge.extract import extract, ImportFailure
from app.agent_knowledge.schemas import url_value
from app.teamspace import jobs
from app.workspace_settings import permissions as p


class Links(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            href=dict(attrs).get('href')
            if href:self.links.append(href)


def queue(db, row, user_id):
    row.status='pending';row.error='';row.revision+=1;row.updated_at=utcnow();row.next_refresh_at=None
    db.commit()
    return jobs.request(db,row.workspace_id,row.teamspace_id,user_id,'knowledge','copilot_source',row.id,{'revision':row.revision},'copilot-source:'+str(row.id)+':'+str(row.revision))


def run(db, job, reader=None):
    reader=reader or WebsiteReader()
    row=db.get(CopilotKnowledge,job.target_id)
    if not row or row.workspace_id!=job.workspace_id or row.revision!=job.payload['revision']:raise HTTPException(409,'The knowledge source changed or was removed.')
    if not (p.allowed(db,row.workspace_id,'manage_copilot',job.actor_id,teamspace_id=row.teamspace_id) or p.allowed(db,row.workspace_id,'manage_ai_agents',job.actor_id,teamspace_id=row.teamspace_id)):raise HTTPException(403,'The source owner no longer has configuration permission.')
    row.status='loading';db.commit()
    try:
        url_value(row.url);origin=urlsplit(row.url);pending=[row.url];seen=set();parts=[];started=time.monotonic()
        while pending and len(seen)<10 and time.monotonic()-started<60:
            url=pending.pop(0)
            if url in seen:continue
            seen.add(url);document=reader.fetch(url)
            final=urlsplit(document.final_url)
            if (final.scheme,final.netloc)!=(origin.scheme,origin.netloc):raise ImportFailure('cross_origin_redirect')
            text=extract(document.data,document.filename);parts.append(document.final_url+'\n'+text)
            if sum(map(len,parts))>200000:raise ImportFailure('text_too_large')
            if row.options.get('scope')=='all' and document.filename.endswith('.html'):
                parser=Links();parser.feed(document.data.decode('utf-8',errors='replace'))
                for href in parser.links:
                    target=urldefrag(urljoin(document.final_url,href))[0];parsed=urlsplit(target)
                    if (parsed.scheme,parsed.netloc)==(origin.scheme,origin.netloc) and target not in seen and target not in pending:
                        url_value(target);pending.append(target)
                        if len(pending)>=100:break
        if not parts:raise ImportFailure('no_text')
        content='\n\n'.join(parts)
        db.refresh(row)
        if row.revision!=job.payload['revision']:raise HTTPException(409,'The source changed while it was loading.')
        row.content=content;row.status='success';row.error='';row.updated_at=utcnow()
        row.options={**row.options,'pages':len(seen),'bounded':bool(pending)}
        if row.options.get('auto_retrain'):row.next_refresh_at=utcnow()+timedelta(days=row.options.get('retrain_days',7))
        db.commit();return {'source_id':str(row.id),'pages':len(seen)}
    except Exception:
        db.rollback();row=db.get(CopilotKnowledge,job.target_id)
        if row and row.revision==job.payload['revision']:
            row.status='fail';row.error='The website could not be trained. Check its public HTTPS access, robots rules and readable content, then retry.'
            if row.options.get('auto_retrain'):row.next_refresh_at=utcnow()+timedelta(days=row.options.get('retrain_days',7))
            db.commit()
        raise HTTPException(422,'The Copilot knowledge source could not be trained.') from None


def schedule(db):
    due=db.scalars(select(CopilotKnowledge).where(CopilotKnowledge.next_refresh_at<=utcnow()).with_for_update(skip_locked=True).limit(10)).all()
    for row in due:
        owner=row.options.get('actor_id')
        if not owner:row.next_refresh_at=None;continue
        queue(db,row,uuid.UUID(owner))
    db.commit()
