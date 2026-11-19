"""Real database coverage of the human ticket repository and signed mail boundary."""
import hashlib
import hmac
import json
import uuid
from datetime import timedelta
import pytest
from pydantic import SecretStr
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import SupportTicket,SupportPerson,TicketEmail,TicketInboundEmail,TicketNote,UsageEvent,SupportJob,Membership,WorkspaceRole,Teamspace,RecordLabels
from app.teamspace import jobs
from app.workspace_settings.usage import source_query
from test_workflows import workflow
from test_create_ticket import base,start_ticket,CaptureTickets,mail_settings
from test_workspace_settings import custom_actor

@pytest.fixture
def desk(client,workflow,app):
    path,_,headers=workflow
    app.state.ticket_mailer=CaptureTickets()
    root=base(path)
    options=client.get(root+'/tickets/workspace-options');assert options.status_code==200,options.text
    return root,headers,options.json()

def create(client,desk,**extra):
    root,h,_=desk
    body={'client_request_id':str(uuid.uuid4()),'subject':'A support case','email':'customer@example.com','name':'Customer',**extra}
    result=client.post(root+'/tickets',json=body,headers=h);assert result.status_code==201,result.text
    return result.json(),body

def reply(client,desk,d,**extra):
    root,h,_=desk
    return client.post(root+'/tickets/'+d['ticket']['id']+'/reply',json={'client_request_id':str(uuid.uuid4()),'expected_revision':d['ticket']['revision'],'content':'Here is the support reply.',**extra},headers=h)

def signed_mail(client,app,root,body,**extra):
    app.state.settings.ticket_mail_webhook_secret=SecretStr('test-mail-signing-secret-more-than-32-characters')
    raw=json.dumps(body).encode();timestamp=str(int(utcnow().timestamp()))
    signature=hmac.new(app.state.settings.ticket_mail_webhook_secret.get_secret_value().encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    return client.post('/api/ticket-mail/'+root.split('/')[3],content=raw,headers={'content-type':'application/json','x-petrix-mail-timestamp':timestamp,'x-petrix-mail-signature':signature,**extra})

def mail_event(**extra):
    return {'event_id':str(uuid.uuid4()),'type':'incoming','occurred_at':utcnow().isoformat(),'message_id':'<incoming-'+uuid.uuid4().hex+'@example.com>','from_email':'customer@example.com','to_email':'hello@petrix.local','subject':'Customer reply','content':'Thank you for your help.',**extra}

def test_manual_creation_sends_nothing_retries_idempotently_and_hides_request_hash(client,desk,app):
    root,h,o=desk;d,body=create(client,desk)
    assert d['emails']==[] and d['incoming']==[] and not app.state.ticket_mailer.messages
    assert d['ticket']['short_id']=='PTX-000001' and d['ticket']['created_by']['type']=='none'
    assert o['default_sender']['email']=='hello@petrix.local'
    assert 'create_payload_hash' not in d['ticket'] and 'client_request_id' not in d['ticket']
    again=client.post(root+'/tickets',json=body,headers=h);assert again.status_code==201 and again.json()['ticket']['id']==d['ticket']['id']
    assert client.post(root+'/tickets',json={**body,'subject':'Changed request'},headers=h).status_code==409
    assert client.get(root+'/tickets').json()['total']==1

@pytest.mark.parametrize('change',[{'email':'not an email'},{'subject':'  '},{'subject':'bad\r\nBcc: other@example.com'},{'sender_domain_id':str(uuid.uuid4())},{'team_ids':[str(uuid.uuid4())]},{'person_id':str(uuid.uuid4())},{'teamspace_id':str(uuid.uuid4())},{'custom_fields':{'missing':'value'}}])
def test_manual_creation_validates_scoped_references_and_headers(client,desk,change):
    root,h,_=desk
    result=client.post(root+'/tickets',json={'client_request_id':str(uuid.uuid4()),'subject':'Support','email':'person@example.com',**change},headers=h)
    assert result.status_code in {403,404,422},result.text
    assert client.get(root+'/tickets').json()['total']==0


def test_default_sender_cc_actual_outbox_alias_snapshot_and_request_id(client,desk,app):
    root,h,_=desk;d,_=create(client,desk)
    body={'client_request_id':str(uuid.uuid4()),'expected_revision':d['ticket']['revision'],'content':'A reply','cc':['copy@example.com','copy@example.com','customer@example.com']}
    result=client.post(root+'/tickets/'+d['ticket']['id']+'/reply',json=body,headers=h);assert result.status_code==200,result.text
    sent=result.json()['emails'][0]
    assert sent['status']=='sent' and sent['email_status']=='sent' and sent['delivery_status']==''
    assert sent['cc']==['copy@example.com'] and sent['sender']=='hello@petrix.local'
    assert result.json()['ticket']['created_by']['type']=='agent'
    assert 'request_hash' not in sent['persona'] and 'default_sender' not in sent['persona']
    assert len(app.state.ticket_mailer.messages)==1
    repeat=client.post(root+'/tickets/'+d['ticket']['id']+'/reply',json=body,headers=h);assert repeat.status_code==200,repeat.text
    assert len(app.state.ticket_mailer.messages)==1
    assert client.post(root+'/tickets/'+d['ticket']['id']+'/reply',json={**body,'cc':['changed@example.com']},headers=h).status_code==409
    assert client.get(root+'/tickets',params={'email_status':'delivered'}).json()['total']==0
    assert client.get(root+'/tickets',params={'created_by':result.json()['ticket']['created_by']['key']}).json()['total']==1


def test_reply_header_injection_closed_revision_and_csrf(client,desk):
    root,h,_=desk;d,_=create(client,desk)
    assert reply(client,desk,d,cc=['valid@example.com\r\nBcc: stolen@example.com']).status_code==422
    assert reply(client,desk,d,expected_revision=99).status_code==409
    assert client.post(root+'/tickets',json={'client_request_id':str(uuid.uuid4()),'subject':'No CSRF','email':'a@example.com'}).status_code==403
    saved=client.post(root+'/tickets/'+d['ticket']['id']+'/details',json={'expected_revision':d['ticket']['revision'],'description':'Updated description','status':'closed'},headers=h);assert saved.status_code==200,saved.text
    assert reply(client,desk,saved.json()).status_code==409


def test_pending_details_search_phone_id_pagination_date_and_limits(client,desk,app):
    root,h,_=desk;d,_=create(client,desk,subject='Literal 100% _ test')
    url=root+'/tickets/'+d['ticket']['id'];body={'expected_revision':d['ticket']['revision'],'description':'Investigation','status':'pending'}
    updated=client.post(url+'/details',json=body,headers=h);assert updated.status_code==200,updated.text
    assert updated.json()['ticket']['status']=='in_progress' and updated.json()['ticket']['display_status']=='pending'
    assert client.post(url+'/details',json=body,headers=h).status_code==409
    assert client.get(root+'/tickets',params={'status':'pending'}).json()['total']==1
    assert client.get(root+'/tickets',params={'status':'in_progress'}).json()['total']==1
    assert client.get(root+'/tickets',params={'q':'% _'}).json()['total']==1
    assert client.get(root+'/tickets',params={'q':d['ticket']['id']}).json()['total']==1
    assert client.get(root+'/tickets',params={'q':'x'*257}).status_code==422
    assert client.get(root+'/tickets',params={'timezone':'missing-zone'}).status_code==422
    assert client.get(root+'/tickets',params={'period':'custom','created_from':'2026-12-01','created_to':'2026-01-01'}).status_code==422
    create(client,desk,subject='Second')
    first=client.get(root+'/tickets',params={'limit':1}).json();second=client.get(root+'/tickets',params={'limit':1,'offset':1}).json()
    assert first['total']==2 and first['has_more'] and first['tickets'][0]['id']!=second['tickets'][0]['id'] and not second['has_more']


def test_notes_are_private_editable_coloured_revision_guarded_and_events_immutable(client,desk,app):
    root,h,_=desk;d,_=create(client,desk);url=root+'/tickets/'+d['ticket']['id']
    saved=client.post(url+'/private-notes',json={'content':'PRIVATE STAFF ONLY','color':'#123456'},headers=h);assert saved.status_code==201,saved.text
    note=next(n for n in saved.json()['notes'] if n['kind']=='note')
    body={'content':'PRIVATE EDIT','color':'#abcdef','expected_revision':note['revision']}
    edit=client.post(url+'/private-notes/'+note['id'],json=body,headers=h);assert edit.status_code==200,edit.text
    assert client.post(url+'/private-notes/'+note['id'],json=body,headers=h).status_code==409
    event=next(n for n in edit.json()['notes'] if n['kind']=='event')
    assert client.post(url+'/private-notes/'+event['id']+'/delete',json={'expected_revision':1},headers=h).status_code==404
    sent=reply(client,desk,edit.json());assert sent.status_code==200,sent.text
    assert 'PRIVATE' not in app.state.ticket_mailer.messages[0]['text']
    with app.state.session_factory() as db:
        t=db.get(SupportTicket,uuid.UUID(d['ticket']['id']))
        assert 'PRIVATE' not in jobs.transcript(db,'tickets',t)
    assert client.post(url+'/private-notes/'+note['id']+'/delete',json={'expected_revision':2},headers=h).status_code==200
    assert not [n for n in client.get(url).json()['notes'] if n['kind']=='note']


def test_manual_ticket_own_permissions_are_independent_of_ai_management(client,desk,app):
    root,h,o=desk;d,_=create(client,desk);user,role=custom_actor(app,root.split('/')[3],['view_tickets_own','manage_tickets_own'])
    # Unassigned tickets are not owned merely because this user created them.
    assert client.get(root+'/tickets').json()['total']==0
    with app.state.session_factory() as db:
        t=db.get(SupportTicket,uuid.UUID(d['ticket']['id']));t.assignee_type='user';t.assignee_id=user;db.commit()
    assert client.get(root+'/tickets').json()['total']==1
    assert client.get(root+'/tickets/'+d['ticket']['id']).json()['can_manage']
    with app.state.session_factory() as db:
        role_row=db.get(WorkspaceRole,role);role_row.permissions=['view_tickets_own'];db.commit()
    assert not client.get(root+'/tickets/'+d['ticket']['id']).json()['can_manage']
    assert client.post(root+'/tickets/'+d['ticket']['id']+'/private-notes',json={'content':'No grant'},headers=h).status_code==404
    assert reply(client,desk,d).status_code==404


def test_manual_creation_requires_current_scoped_manage_and_view(client,desk,app):
    root,h,o=desk;user,role=custom_actor(app,root.split('/')[3],['view_tickets_own','manage_tickets_own'])
    d,_=create(client,desk,user_id=str(user))
    assert d['can_manage']
    assert client.post(root+'/tickets',json={'client_request_id':str(uuid.uuid4()),'subject':'Invisible','email':'person@example.com'},headers=h).status_code==403
    assert client.get(root+'/tickets').json()['total']==1


def test_linked_chat_custom_fields_copy_and_explicit_override_are_independent(client,desk,workflow,app):
    from test_custom_fields import create_field,body as field_body
    root,h,_=desk;_,field=create_field(client,workflow[0],h,field_body('Rating','number'))
    path,h,response=start_ticket(client,workflow);chat=response.json()['conversation_id']
    with app.state.session_factory() as db:
        from app.models import AgentConversation
        c=db.get(AgentConversation,uuid.UUID(chat));c.custom_fields={field['key']:{'value':2,'name':'Rating'}};db.commit()
    d,_=create(client,desk,conversation_id=chat,email='',custom_fields={field['key']:5})
    assert d['ticket']['custom_fields'][field['key']]['value']==5
    with app.state.session_factory() as db:
        from app.models import AgentConversation
        c=db.get(AgentConversation,uuid.UUID(chat));assert c.custom_fields[field['key']]['value']==2


def test_signed_receipts_not_smtp_acceptance_control_delivery_filter_and_order(client,desk,app):
    root,h,_=desk;d,_=create(client,desk);sent=reply(client,desk,d);assert sent.status_code==200,sent.text
    e=sent.json()['emails'][0]
    body=mail_event(type='delivered',message_id=e['message_id'])
    assert signed_mail(client,app,root,body).status_code==200
    assert signed_mail(client,app,root,body).json()['duplicate']
    assert signed_mail(client,app,root,{**body,'type':'bounced'}).status_code==409
    assert client.get(root+'/tickets',params={'email_status':'delivered'}).json()['total']==1
    older=mail_event(type='bounced',message_id=e['message_id'],occurred_at=(utcnow()-timedelta(hours=1)).isoformat())
    assert signed_mail(client,app,root,older).status_code==200
    assert client.get(root+'/tickets',params={'email_status':'delivered'}).json()['total']==1
    newer=mail_event(type='bounced',message_id=e['message_id'],occurred_at=(utcnow()+timedelta(seconds=1)).isoformat())
    assert signed_mail(client,app,root,newer).status_code==200
    assert client.get(root+'/tickets',params={'email_status':'failed_to_deliver'}).json()['total']==1


def test_signed_incoming_thread_cc_dedup_reopens_and_counts_once(client,desk,app):
    root,h,_=desk;d,_=create(client,desk);sent=reply(client,desk,d,cc=['copy@example.com']);assert sent.status_code==200,sent.text
    detail=sent.json();url=root+'/tickets/'+d['ticket']['id'];e=detail['emails'][0]
    closed=client.post(url+'/details',json={'expected_revision':detail['ticket']['revision'],'description':'','status':'closed'},headers=h);assert closed.status_code==200,closed.text
    body=mail_event(in_reply_to=e['message_id'],from_email='copy@example.com')
    assert signed_mail(client,app,root,body).status_code==200
    assert signed_mail(client,app,root,body).json()['duplicate']
    assert signed_mail(client,app,root,{**body,'event_id':str(uuid.uuid4())}).status_code==200
    d=client.get(url).json();assert d['ticket']['status']=='open' and len(d['incoming'])==1
    assert d['incoming'][0]['content']==body['content']
    assert signed_mail(client,app,root,mail_event(in_reply_to=e['message_id'],from_email='outsider@example.com')).status_code==422
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(TicketInboundEmail))==1
        assert db.scalar(select(func.sum(source_query(uuid.UUID(root.split('/')[3])).c.units)))==2
    assert len(app.state.ticket_mailer.messages)==1


def test_mail_adapter_is_disabled_by_default_requires_fresh_signature_and_bounds(client,desk,app):
    root,h,_=desk;url='/api/ticket-mail/'+root.split('/')[3]
    assert client.post(url,json=mail_event()).status_code==503
    assert signed_mail(client,app,root,mail_event(),**{'x-petrix-mail-signature':'wrong'}).status_code==401
    assert signed_mail(client,app,root,mail_event(),**{'x-petrix-mail-timestamp':'0'}).status_code==401
    assert signed_mail(client,app,root,mail_event(subject='bad\r\nheader')).status_code==422
    assert signed_mail(client,app,root,mail_event(to_email='unverified@example.com')).status_code==422
    assert signed_mail(client,app,root,mail_event(occurred_at='2026-01-01T00:00:00')).status_code==422


def test_first_incoming_creates_email_case_without_ai_or_outbound_and_sets_creator(client,desk,app):
    root,h,_=desk;body=mail_event(name='Incoming Customer')
    incoming=signed_mail(client,app,root,body);assert incoming.status_code==200,incoming.text
    d=client.get(root+'/tickets/'+incoming.json()['ticket_id']).json()
    assert d['ticket']['origin']=='email' and d['ticket']['created_by']['type']=='person' and len(d['incoming'])==1 and not d['emails']
    assert not app.state.ticket_mailer.messages
    assert client.get(root+'/tickets',params={'created_by':d['ticket']['created_by']['key']}).json()['total']==1


def test_delete_all_or_nothing_stale_inflight_and_preserves_interactions(client,desk,app):
    root,h,_=desk;d,_=create(client,desk);sent=reply(client,desk,d);assert sent.status_code==200,sent.text
    body=mail_event(ticket_id=d['ticket']['id']);assert signed_mail(client,app,root,body).status_code==200
    d=client.get(root+'/tickets/'+d['ticket']['id']).json();t=d['ticket'];uid=uuid.UUID(root.split('/')[3])
    selected={'ids':[t['id']],'revisions':{t['id']:t['revision']}}
    assert client.post(root+'/tickets/delete',json={**selected,'revisions':{t['id']:1}},headers=h).status_code==409
    with app.state.session_factory() as db:
        email=db.scalar(select(TicketEmail));email.status='sending';db.commit()
    assert client.post(root+'/tickets/delete',json=selected,headers=h).status_code==409
    with app.state.session_factory() as db:
        email=db.scalar(select(TicketEmail));email.status='sent';db.commit()
        count=db.scalar(select(func.sum(source_query(uid).c.units)))
        person=db.get(SupportTicket,uuid.UUID(t['id'])).person_id
    result=client.post(root+'/tickets/delete',json=selected,headers=h);assert result.status_code==200,result.text
    assert client.get(root+'/tickets/'+t['id']).status_code==404
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(TicketEmail))==0 and db.scalar(select(func.count()).select_from(TicketInboundEmail))==0
        assert db.get(SupportPerson,person) and db.scalar(select(func.sum(source_query(uid).c.units)))==count
    # A replay after deletion must not recreate the ticket or count the email twice.
    assert signed_mail(client,app,root,body).json()['duplicate']
    assert client.get(root+'/tickets').json()['total']==0


def test_people_lookup_requires_person_or_ticket_visibility(client,desk,app):
    root,h,_=desk;d,_=create(client,desk)
    assert client.get(root+'/tickets/people',params={'q':'Customer'}).json()['people']
    user,role=custom_actor(app,root.split('/')[3],['view_tickets_own','manage_tickets_own'])
    assert client.get(root+'/tickets/people',params={'q':'Customer'}).json()['people']==[]
    assert client.get(root+'/tickets/'+d['ticket']['id']).status_code==404


def test_transfer_requires_separate_grant_and_removes_previous_team_audience(client,desk,app):
    root,h,_=desk;d,_=create(client,desk);user,role=custom_actor(app,root.split('/')[3],['view_tickets_all','manage_tickets_all'])
    url=root+'/tickets/'+d['ticket']['id']+'/transfer';body={'expected_revision':d['ticket']['revision'],'user_id':str(user),'team_ids':[]}
    assert client.post(url,json=body,headers=h).status_code==403
    with app.state.session_factory() as db:
        role_row=db.get(WorkspaceRole,role);role_row.permissions=[*role_row.permissions,'transfer_tickets'];t=db.get(SupportTicket,uuid.UUID(d['ticket']['id']));t.audience_team_ids=[str(uuid.uuid4())];db.commit()
    result=client.post(url,json=body,headers=h);assert result.status_code==200,result.text
    assert result.json()['ticket']['assigned_user_id']==str(user) and result.json()['ticket']['audience_team_ids']==[]


def test_ticket_analysis_requires_closed_case(client,desk):
    root,h,_=desk;d,_=create(client,desk)
    result=client.post(root+'/support/tickets/'+d['ticket']['id']+'/analyze',json={'request_id':str(uuid.uuid4())},headers=h)
    assert result.status_code==409 and 'Close or resolve' in result.text


def test_confirmed_receipt_resolves_uncertain_send_and_accounts_once(client,desk,app):
    root,h,_=desk;d,_=create(client,desk);sent=reply(client,desk,d);assert sent.status_code==200
    e=sent.json()['emails'][0]
    with app.state.session_factory() as db:
        row=db.get(TicketEmail,uuid.UUID(e['id']));row.status='uncertain';row.sent_at=None;db.commit()
    body=mail_event(type='delivered',message_id=e['message_id'])
    result=signed_mail(client,app,root,body);assert result.status_code==200,result.text
    detail=client.get(root+'/tickets/'+d['ticket']['id']).json()
    assert detail['emails'][0]['status']=='sent' and detail['emails'][0]['email_status']=='delivered'
    with app.state.session_factory() as db:
        assert db.scalar(select(func.sum(source_query(uuid.UUID(root.split('/')[3])).c.units)))==1
    assert len(app.state.ticket_mailer.messages)==1


def test_delete_selection_is_atomic_and_new_provider_event_cannot_resurrect_deleted_email(client,desk,app):
    root,h,_=desk;d,_=create(client,desk);identifier=d['ticket']['id'];unknown=str(uuid.uuid4())
    result=client.post(root+'/tickets/delete',json={'ids':[identifier,unknown],'revisions':{identifier:d['ticket']['revision'],unknown:1}},headers=h)
    assert result.status_code==404 and client.get(root+'/tickets/'+identifier).status_code==200
    body=mail_event(ticket_id=identifier);assert signed_mail(client,app,root,body).status_code==200
    d=client.get(root+'/tickets/'+identifier).json()
    assert client.post(root+'/tickets/delete',json={'ids':[identifier],'revisions':{identifier:d['ticket']['revision']}},headers=h).status_code==200
    replay=signed_mail(client,app,root,{**body,'event_id':str(uuid.uuid4())});assert replay.status_code==200 and replay.json()['duplicate']
    assert client.get(root+'/tickets').json()['total']==0


def test_manage_grant_without_view_cannot_mutate_or_send(client,desk,app):
    root,h,_=desk;d,_=create(client,desk);custom_actor(app,root.split('/')[3],['manage_tickets_all'])
    assert client.get(root+'/tickets').status_code==403
    assert client.post(root+'/tickets/'+d['ticket']['id']+'/private-notes',json={'content':'Hidden'},headers=h).status_code==404
    assert reply(client,desk,d).status_code==404


def test_multiple_team_scope_transfer_revokes_previous_audience(client,desk,app):
    from app.models import SupportTeam
    root,h,o=desk;workspace=uuid.UUID(root.split('/')[3])
    with app.state.session_factory() as db:
        owner=db.scalar(select(Membership).where(Membership.workspace_id==workspace,Membership.role=='owner'))
        a=SupportTeam(workspace_id=workspace,name='Team A',member_ids=[str(owner.user_id)],alternative_names=[],description='')
        b=SupportTeam(workspace_id=workspace,name='Team B',member_ids=[],alternative_names=[],description='');db.add_all([a,b]);db.flush();aid,bid=str(a.id),str(b.id);db.commit()
    d,_=create(client,desk,team_ids=[aid]);user,_=custom_actor(app,str(workspace),['view_tickets_teams','manage_tickets_teams','transfer_tickets'])
    assert client.get(root+'/tickets').json()['total']==1
    transferred=client.post(root+'/tickets/'+d['ticket']['id']+'/transfer',json={'expected_revision':d['ticket']['revision'],'team_ids':[bid]},headers=h)
    assert transferred.status_code==200 and transferred.json()=={'transferred':True},transferred.text
    assert client.get(root+'/tickets').json()['total']==0
    assert client.get(root+'/tickets/'+d['ticket']['id']).status_code==404


def test_manual_ticket_space_is_enforced_even_without_linked_chat(client,desk,app):
    root,h,o=desk;d,_=create(client,desk);user,_=custom_actor(app,root.split('/')[3],['view_tickets_all','manage_tickets_all'])
    with app.state.session_factory() as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==uuid.UUID(root.split('/')[3]),Membership.user_id==user));member.teamspace_ids=[];db.commit()
    assert client.get(root+'/tickets').json()['total']==0
    assert client.get(root+'/tickets/'+d['ticket']['id']).status_code==404


def test_oversized_mail_payload_is_rejected_before_parsing(client,desk,app):
    root,h,_=desk
    assert signed_mail(client,app,root,mail_event(content='X'*270000)).status_code==413


def test_takeover_and_send_requires_transfer_and_changes_assignment_atomically(client,desk,app):
    root,h,_=desk;d,_=create(client,desk);user,role=custom_actor(app,root.split('/')[3],['view_tickets_all','manage_tickets_all'])
    assert reply(client,desk,d,takeover=True).status_code==403
    assert not app.state.ticket_mailer.messages
    with app.state.session_factory() as db:
        row=db.get(WorkspaceRole,role);row.permissions=[*row.permissions,'transfer_tickets'];db.commit()
    sent=reply(client,desk,d,takeover=True);assert sent.status_code==200,sent.text
    assert sent.json()['ticket']['assigned_user_id']==str(user) and len(app.state.ticket_mailer.messages)==1


@pytest.mark.parametrize('status',['open','overdue'])
def test_team_cannot_be_deleted_while_assigned_to_unfinished_agent_owned_ticket(client,desk,status):
    root,h,o=desk;uid=client.get('/api/auth/me').json()['user']['id']
    team=client.post(root+'/teams',json={'name':'Assigned support','description':'','alternative_names':[],'member_ids':[uid]},headers=h);assert team.status_code==201,team.text;team=team.json()
    d,_=create(client,desk,user_id=uid,team_ids=[team['id']]);url=root+'/tickets/'+d['ticket']['id']+'/details'
    if status=='overdue':
        changed=client.post(url,json={'expected_revision':d['ticket']['revision'],'description':'','status':'overdue'},headers=h);assert changed.status_code==200,changed.text;d=changed.json()
    assert client.post(root+'/teams/'+team['id']+'/delete',json={'expected_revision':team['revision']},headers=h).status_code==409
    assert client.post(url,json={'expected_revision':d['ticket']['revision'],'description':'','status':'closed'},headers=h).status_code==200
    assert client.post(root+'/teams/'+team['id']+'/delete',json={'expected_revision':team['revision']},headers=h).status_code in {200,204}


def test_same_reply_request_id_on_different_tickets_has_unique_message_id(client,desk,app):
    root,h,_=desk;a,_=create(client,desk,subject='Case A');b,_=create(client,desk,subject='Case B');request=str(uuid.uuid4())
    one=reply(client,desk,a,client_request_id=request);two=reply(client,desk,b,client_request_id=request)
    assert one.status_code==two.status_code==200
    ea,eb=one.json()['emails'][0],two.json()['emails'][0]
    assert ea['message_id']!=eb['message_id']
    event=mail_event(in_reply_to=eb['message_id']);result=signed_mail(client,app,root,event)
    assert result.status_code==200 and result.json()['ticket_id']==b['ticket']['id']
    assert client.get(root+'/tickets/'+a['ticket']['id']).json()['incoming']==[]


def test_ambiguous_legacy_message_id_requires_explicit_matching_ticket(client,desk,app):
    root,h,_=desk;a,_=create(client,desk,subject='Case A');b,_=create(client,desk,subject='Case B')
    ea=reply(client,desk,a).json()['emails'][0];eb=reply(client,desk,b).json()['emails'][0]
    with app.state.session_factory() as db:
        row=db.get(TicketEmail,uuid.UUID(eb['id']));row.message_id=ea['message_id'];db.commit()
    event=mail_event(in_reply_to=ea['message_id'])
    assert signed_mail(client,app,root,event).status_code==409
    result=signed_mail(client,app,root,{**event,'ticket_id':b['ticket']['id']})
    assert result.status_code==200 and result.json()['ticket_id']==b['ticket']['id']
