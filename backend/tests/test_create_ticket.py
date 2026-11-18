from copy import deepcopy
import uuid
import pytest
from sqlalchemy import select
from app.models import SupportTicket,TicketEmail,AgentConversation,TicketEmailDomain
from app.tickets.notifications import DeliveryError
from test_workflows import workflow,node,event,save,message,contents
from test_branch import variable
from test_custom_fields import create_field,body as field_body,groups


def base(path):return path.rsplit('/agents/',1)[0]


def ticket_config(**extra):
    return dict(subject='Complaint for {customer_name}',description='The transfer failed.',full_name='{customer_name}',email='chris@example.com',phone='',tag_ids=[],tag_group_ids=[],assignee_type='none',assignee_id=None,email_domain_id=None,customer_template_id=None,assignee_template_id=None,custom_fields=[],**extra)


def start_ticket(client,workflow,config=None,children=None,identifier=None):
    path,document,headers=workflow
    document['nodes'][0]['children']=[variable('customer_name','"Chris"'),node('action','create_ticket',config=config or ticket_config()),*(children or [message('Ticket {last_ticket.reference} was logged.')])]
    saved=save(client,path,document,headers);assert saved.status_code==200,saved.text
    response=event(client,path,headers,**({'client_event_id':identifier} if identifier else {}));assert response.status_code==200,response.text
    return path,headers,response


def create_setting(client,path,headers,kind,data):
    response=client.post(base(path)+'/ticket-settings/'+kind,json=data,headers=headers);assert response.status_code==201,response.text
    return response.json()


class CaptureTickets:
    def __init__(self,error=None):self.messages=[];self.error=error
    def send_ticket(self,row):
        self.messages.append({'recipient':row.recipient,'subject':row.subject,'html':row.body_html,'text':row.body_text,'message_id':row.message_id})
        if self.error:raise self.error


def mail_settings(client,workflow,app):
    path,_,headers=workflow
    domain=create_setting(client,path,headers,'email-domains',{'domain':'mail.example.com','verification_method':'txt'})
    class DNS:
        def records(self,name,kind):return [domain['dns_record']['value']]
    app.state.ticket_dns=DNS()
    verified=client.post(base(path)+'/ticket-settings/email-domains/'+domain['id']+'/verify',json={'expected_revision':domain['revision']},headers=headers)
    assert verified.status_code==200 and verified.json()['verified'],verified.text
    template=create_setting(client,path,headers,'email-templates',{'name':'Receipt','content':'<p>Hello {ticket.full_name},</p><p>Reference {ticket.reference}: {ticket.subject}</p>'})
    app.state.ticket_mailer=CaptureTickets()
    return verified.json(),template


def test_ticket_is_ready_persistent_and_does_not_close_chat(client,workflow,app):
    path,headers,response=start_ticket(client,workflow)
    data=response.json();ticket=data['last_ticket'];assert ticket['reference']=='PTX-000001' and not data['closed']
    assert contents(response)==['Ticket PTX-000001 was logged.']
    listing=client.get(base(path)+'/tickets').json();assert len(listing['tickets'])==1
    record=listing['tickets'][0];assert record['email']=='chris@example.com' and record['status']=='open' and record['full_name']=='Chris'
    detail=client.get(base(path)+'/tickets/'+ticket['id']).json();assert detail['emails']==[] and detail['notes'][0]['kind']=='event'
    with app.state.session_factory() as db:
        assert len(db.scalars(select(SupportTicket)).all())==1
        conversation=db.get(AgentConversation,uuid.UUID(data['conversation_id']));assert not conversation.workflow_state.get('closed')


def test_initial_email_uses_verified_sender_and_escaped_variables(client,workflow,app):
    domain,template=mail_settings(client,workflow,app)
    config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id'])
    path,headers,response=start_ticket(client,workflow,config)
    detail=client.get(base(path)+'/tickets/'+response.json()['last_ticket']['id']).json()
    assert detail['emails'][0]['status']=='sent' and len(app.state.ticket_mailer.messages)==1
    sent=app.state.ticket_mailer.messages[0];assert sent['recipient']=='chris@example.com' and 'PTX-000001' in sent['html']
    assert '[PTX-000001] Complaint for Chris'==sent['subject']


def test_notification_failure_keeps_ticket_and_requires_explicit_retry(client,workflow,app):
    domain,template=mail_settings(client,workflow,app);app.state.ticket_mailer=CaptureTickets(DeliveryError(False))
    config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id'])
    path,headers,response=start_ticket(client,workflow,config);ticket_id=response.json()['last_ticket']['id'];url=base(path)+'/tickets/'+ticket_id
    detail=client.get(url).json();mail=detail['emails'][0]
    assert mail['status']=='failed' and detail['ticket']['status']=='open'
    assert client.get(url).json()['emails'][0]['attempts']==1
    app.state.ticket_mailer=CaptureTickets()
    retried=client.post(url+'/emails/'+mail['id']+'/retry',json={'expected_revision':mail['revision']},headers=headers)
    assert retried.status_code==200 and retried.json()['emails'][0]['status']=='sent',retried.text
    assert len(client.get(base(path)+'/tickets').json()['tickets'])==1


def test_uncertain_email_is_not_replayed_without_review(client,workflow,app):
    domain,template=mail_settings(client,workflow,app);app.state.ticket_mailer=CaptureTickets(DeliveryError(True))
    config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id'])
    path,headers,response=start_ticket(client,workflow,config);url=base(path)+'/tickets/'+response.json()['last_ticket']['id'];mail=client.get(url).json()['emails'][0]
    assert mail['status']=='uncertain'
    assert client.post(url+'/emails/'+mail['id']+'/retry',json={'expected_revision':mail['revision']},headers=headers).status_code==409
    app.state.ticket_mailer=CaptureTickets()
    assert client.post(url+'/emails/'+mail['id']+'/retry',json={'expected_revision':mail['revision'],'confirm_not_sent':True},headers=headers).json()['emails'][0]['status']=='sent'


def test_custom_fields_copy_and_action_override_do_not_change_chat(client,workflow):
    path,_,headers=workflow
    _,field=create_field(client,path,headers,{**field_body(),'default_value':'accounts'})
    config=ticket_config();config['custom_fields']=[{'field_id':field['id'],'value':'mobile_banking'}]
    path,headers,response=start_ticket(client,workflow,config)
    ticket=client.get(base(path)+'/tickets/'+response.json()['last_ticket']['id']).json()['ticket']
    assert response.json()['custom_fields'][field['key']]['value']=='accounts'
    assert ticket['custom_fields'][field['key']]['value']=='mobile_banking' and ticket['custom_fields'][field['key']]['source']=='create_ticket'
    assert client.post(base(path)+'/custom-fields/delete',json={'fields':[{'id':field['id'],'expected_revision':field['revision']}]},headers=headers).status_code==409


def test_tags_expand_groups_and_preserve_historical_labels(client,workflow):
    path,_,headers=workflow
    group=create_setting(client,path,headers,'groups',{'name':'Complaints'})
    tag=create_setting(client,path,headers,'tags',{'name':'Banking','group_id':group['id']})
    config=ticket_config();config['tag_group_ids']=[group['id']]
    path,headers,response=start_ticket(client,workflow,config)
    ticket=client.get(base(path)+'/tickets/'+response.json()['last_ticket']['id']).json()['ticket'];assert ticket['tags'][0]['name']=='Banking'
    assert client.post(base(path)+'/ticket-settings/groups/'+group['id']+'/delete',json={'expected_revision':group['revision']},headers=headers).status_code==409


def test_dns_wrong_answer_keeps_domain_unverified(client,workflow,app):
    path,_,headers=workflow;domain=create_setting(client,path,headers,'email-domains',{'domain':'mail.example.com','verification_method':'cname'})
    class DNS:
        def records(self,*args):return ['wrong.example.com']
    app.state.ticket_dns=DNS()
    checked=client.post(base(path)+'/ticket-settings/email-domains/'+domain['id']+'/verify',json={'expected_revision':1},headers=headers)
    assert checked.status_code==200 and not checked.json()['verified']
    config=ticket_config();config['email_domain_id']=domain['id']
    document=client.get(path+'/workflows').json();document['nodes'][0]['children']=[node('action','create_ticket',config=config)]
    assert save(client,path,document,headers).status_code==422


def test_ticket_assignment_status_and_internal_notes(client,workflow):
    path,headers,response=start_ticket(client,workflow);url=base(path)+'/tickets/'+response.json()['last_ticket']['id'];detail=client.get(url).json()
    updated=client.post(url,json={'expected_revision':detail['ticket']['revision'],'status':'in_progress','assignee_type':'none'},headers=headers)
    assert updated.status_code==200 and updated.json()['ticket']['status']=='in_progress',updated.text
    stale=client.post(url,json={'expected_revision':1,'status':'closed'},headers=headers);assert stale.status_code==409
    note=client.post(url+'/notes',json={'content':'Investigation started.'},headers=headers);assert note.status_code==201
    assert note.json()['notes'][-1]['content']=='Investigation started.'
    assert client.post(url+'/notes',json={'content':'No CSRF'}).status_code==403


def test_same_event_id_reuses_ticket_instead_of_recreating(client,workflow):
    from test_workflows import intent
    from test_jump import jump
    path,document,headers=workflow;identifier=str(uuid.uuid4())
    action=node('action','create_ticket',config=ticket_config())
    document['nodes'][0]['children']=[variable('customer_name','"Chris"'),action]
    document['nodes'].append(intent('again',[jump(action)]))
    assert save(client,path,document,headers).status_code==200
    first=event(client,path,headers,client_event_id=identifier).json()
    second=event(client,path,headers,'message_received',first['conversation_id'],content='again',client_event_id=identifier)
    assert second.status_code==200 and second.json()['last_ticket']['id']==first['last_ticket']['id'],second.text
    assert len(client.get(base(path)+'/tickets').json()['tickets'])==1
    third=event(client,path,headers,'message_received',first['conversation_id'],content='again',client_event_id=str(uuid.uuid4()))
    assert third.status_code==200 and third.json()['last_ticket']['reference']=='PTX-000002',third.text


@pytest.mark.parametrize('change',[{'email':'invalid'},{'full_name':'Chris\nInjected'},{'subject':'Bad\r\nBcc: x@example.com'}])
def test_invalid_ticket_values_do_not_create_records(client,workflow,change):
    path,document,headers=workflow;config=ticket_config();config.update(change)
    document['nodes'][0]['children']=[variable('customer_name','"Chris"'),node('action','create_ticket',config=config)]
    response=save(client,path,document,headers)
    if response.status_code==200:assert event(client,path,headers).status_code==422
    else:assert response.status_code==422
    assert client.get(base(path)+'/tickets').json()['tickets']==[]


def test_cname_verification_case_and_trailing_dot(client,workflow,app):
    path,_,headers=workflow;row=create_setting(client,path,headers,'email-domains',{'domain':'MAIL.Example.COM'})
    class DNS:
        def records(self,*args):return [row['dns_record']['value'].upper()+'.']
    app.state.ticket_dns=DNS()
    verified=client.post(base(path)+'/ticket-settings/email-domains/'+row['id']+'/verify',json={'expected_revision':1},headers=headers)
    assert verified.status_code==200 and verified.json()['verified']
    assert client.post(base(path)+'/ticket-settings/email-domains/'+row['id']+'/verify',json={'expected_revision':1},headers=headers).status_code==409


def test_switching_dns_method_revokes_verification(client,workflow,app):
    domain,template=mail_settings(client,workflow,app);path,_,headers=workflow
    response=client.post(base(path)+'/ticket-settings/email-domains/'+domain['id'],json={'expected_revision':domain['revision'],'domain':domain['domain'],'verification_method':'cname'},headers=headers)
    assert response.status_code==200 and not response.json()['verified'] and response.json()['dns_record']['type']=='CNAME'


def test_expired_sender_cannot_be_used(client,workflow,app):
    from datetime import timedelta
    from app.core.security import utcnow
    domain,template=mail_settings(client,workflow,app);path,document,headers=workflow
    with app.state.session_factory() as db:
        row=db.get(TicketEmailDomain,uuid.UUID(domain['id']));row.verified_at=utcnow()-timedelta(days=31);db.commit()
    config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id'])
    document['nodes'][0]['children']=[node('action','create_ticket',config=config)]
    assert save(client,path,document,headers).status_code==422


def test_template_sanitization_and_escaped_customer_html(client,workflow,app):
    domain,template=mail_settings(client,workflow,app);path,document,headers=workflow
    unsafe=create_setting(client,path,headers,'email-templates',{'name':'Safety test','content':'<script>bad()</script><p>{ticket.full_name}</p><img src="https://tracker.example/image"><a href="javascript:bad()">Unsafe link</a><a href="https://[broken">Broken</a>'})
    assert 'script' not in unsafe['content'] and '<img' not in unsafe['content'] and 'javascript:' not in unsafe['content']
    config=ticket_config();config.update(full_name='<b>Chris & Co</b>',email_domain_id=domain['id'],customer_template_id=unsafe['id'])
    path,headers,response=start_ticket(client,workflow,config)
    html=app.state.ticket_mailer.messages[0]['html'];assert '&lt;b&gt;Chris &amp; Co&lt;/b&gt;' in html and '<b>Chris' not in html


def test_later_workflow_error_rolls_back_ticket_and_queued_emails(client,workflow,app):
    domain,template=mail_settings(client,workflow,app);path,document,headers=workflow
    config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id'])
    document['nodes'][0]['children']=[variable('customer_name','"Chris"'),node('action','create_ticket',config=config),variable('failure','1/0')]
    assert save(client,path,document,headers).status_code==200
    assert event(client,path,headers).status_code==422
    assert client.get(base(path)+'/tickets').json()['tickets']==[] and app.state.ticket_mailer.messages==[]
    with app.state.session_factory() as db:assert db.scalars(select(TicketEmail)).all()==[]


def test_create_ticket_works_under_chat_closed(client,workflow):
    from test_chat_closed import closed_root
    path,document,headers=workflow;document['nodes'][0]['children']=[variable('customer_name','"Chris"')]
    closed_root(document)['children']=[node('action','create_ticket',config=ticket_config())]
    assert save(client,path,document,headers).status_code==200
    started=event(client,path,headers).json();closed=event(client,path,headers,'chat_closed',started['conversation_id'])
    assert closed.status_code==200 and closed.json()['closed'] and closed.json()['last_ticket']['reference']=='PTX-000001',closed.text
    assert event(client,path,headers,'chat_closed',started['conversation_id']).status_code==409
    assert len(client.get(base(path)+'/tickets').json()['tickets'])==1


def test_llm_summary_is_used_in_ticket_description(client,workflow,app):
    from test_llm_processing import StubProvider,llm
    app.state.llm_processing_client=StubProvider();path,document,headers=workflow
    config=ticket_config();config['description']='{complaint_details}'
    document['nodes'][0]['children']=[variable('customer_name','"Chris"'),llm(success=[node('action','create_ticket',config=config)])]
    assert save(client,path,document,headers).status_code==200
    response=event(client,path,headers);assert response.status_code==200,response.text
    detail=client.get(base(path)+'/tickets/'+response.json()['last_ticket']['id']).json()
    assert detail['ticket']['description']=='Customer requests a refund.'


def test_ticket_setting_references_include_disabled_actions(client,workflow,app):
    domain,template=mail_settings(client,workflow,app);path,document,headers=workflow
    config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id']);action=node('action','create_ticket',config=config);action['enabled']=False
    document['nodes'][0]['children']=[action];assert save(client,path,document,headers).status_code==200
    for kind,row in [('email-domains',domain),('email-templates',template)]:
        assert client.post(base(path)+'/ticket-settings/'+kind+'/'+row['id']+'/delete',json={'expected_revision':row['revision']},headers=headers).status_code==409


def test_member_sees_only_assigned_tickets_and_loses_access_after_revocation(client,workflow,workers,app):
    from app.models import Membership
    worker,worker_headers,user_id=workers(1);other,_,_=workers(2)
    config=ticket_config();config.update(assignee_type='user',assignee_id=user_id)
    path,headers,response=start_ticket(client,workflow,config);url=base(path)+'/tickets/'+response.json()['last_ticket']['id']
    assert worker.get(url).status_code==200 and len(worker.get(base(path)+'/tickets').json()['tickets'])==1
    assert other.get(url).status_code==404 and other.get(base(path)+'/tickets').json()['tickets']==[]
    assert worker.post(base(path)+'/ticket-settings/tags',json={'name':'Forbidden'},headers=worker_headers).status_code==403
    with app.state.session_factory() as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==uuid.UUID(base(path).split('/')[3]),Membership.user_id==uuid.UUID(user_id)));db.delete(membership);db.commit()
    assert worker.get(url).status_code==404


def test_team_receives_one_notification_per_current_member(client,workflow,workers,app):
    from test_live_agent_handoff import support
    worker,_,user_id=workers(1);other,_,other_id=workers(2)
    path,_,headers=workflow;_,team=support(client,path,headers,[user_id,other_id])
    domain,template=mail_settings(client,workflow,app)
    config=ticket_config();config.update(assignee_type='team',assignee_id=team['id'],email_domain_id=domain['id'],assignee_template_id=template['id'])
    path,headers,response=start_ticket(client,workflow,config)
    assert {mail['recipient'] for mail in app.state.ticket_mailer.messages}=={'support1@example.com','support2@example.com'}
    assert worker.get(base(path)+'/tickets/'+response.json()['last_ticket']['id']).status_code==200
    assert other.get(base(path)+'/tickets/'+response.json()['last_ticket']['id']).status_code==200
    assert client.post(base(path)+'/teams/'+team['id']+'/delete',json={'expected_revision':team['revision']},headers=headers).status_code==409


def test_queued_email_rechecks_sender_before_delivery(client,workflow,app,monkeypatch):
    from app.tickets import notifications
    domain,template=mail_settings(client,workflow,app)
    config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id'])
    dispatch=notifications.dispatch
    with monkeypatch.context() as patch:
        patch.setattr(notifications,'dispatch',lambda *args,**kwargs:0)
        path,headers,response=start_ticket(client,workflow,config)
    with app.state.session_factory() as db:
        row=db.get(TicketEmailDomain,uuid.UUID(domain['id']));row.enabled=False;db.commit()
    url=base(path)+'/tickets/'+response.json()['last_ticket']['id'];sent=client.post(url+'/send-queued',json={},headers=headers)
    assert sent.status_code==200 and sent.json()['emails'][0]['status']=='failed'
    assert app.state.ticket_mailer.messages==[]


def test_setting_archive_keeps_ticket_field_and_tag_history(client,workflow):
    path,_,headers=workflow;tag=create_setting(client,path,headers,'tags',{'name':'Old tag'})
    config=ticket_config();config['tag_ids']=[tag['id']]
    path,headers,response=start_ticket(client,workflow,config);url=base(path)+'/tickets/'+response.json()['last_ticket']['id']
    document=client.get(path+'/workflows').json();document['nodes'][0]['children']=[];assert save(client,path,document,headers).status_code==200
    assert client.post(base(path)+'/ticket-settings/tags/'+tag['id']+'/delete',json={'expected_revision':tag['revision']},headers=headers).status_code==204
    assert client.get(url).json()['ticket']['tags'][0]['name']=='Old tag'


from test_live_agent_handoff import workers


def test_sender_expiring_after_configuration_does_not_lose_ticket(client,workflow,app):
    path,document,headers=workflow;domain,template=mail_settings(client,workflow,app)
    config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id'])
    document['nodes'][0]['children']=[variable('customer_name','"Chris"'),node('action','create_ticket',config=config)]
    assert save(client,path,document,headers).status_code==200
    with app.state.session_factory() as db:
        row=db.get(TicketEmailDomain,uuid.UUID(domain['id']));row.enabled=False;db.commit()
    response=event(client,path,headers);assert response.status_code==200,response.text
    detail=client.get(base(path)+'/tickets/'+response.json()['last_ticket']['id']).json()
    assert detail['ticket']['status']=='open' and detail['emails'][0]['status']=='failed'
    assert app.state.ticket_mailer.messages==[]


@pytest.mark.parametrize('setting',[{'domain':'https://mail.example.com'},{'domain':'127.0.0.1'},{'domain':'mail.example.com','local_part':'bad name'},{'domain':'mail.example.com','from_name':'Bad\nBcc: other@example.com'},{'domain':'mail.example.com','verification_method':'mx'},{'domain':'mail.example.com','enabled':'yes'}])
def test_invalid_domain_settings_are_rejected(client,workflow,setting):
    path,_,headers=workflow
    assert client.post(base(path)+'/ticket-settings/email-domains',json=setting,headers=headers).status_code==422


def test_typed_and_dynamic_custom_field_overrides(client,workflow):
    path,document,headers=workflow
    definitions=[]
    for name,type,value in [('Score','number',42),('Contact later','toggle',False),('Date','date','2026-11-13'),('Note','text','A customer note')]:
        _,field=create_field(client,path,headers,field_body(name,type));definitions.append((field,value))
    config=ticket_config();config['custom_fields']=[{'field_id':f['id'],'value':value if f['type']!='number' else '{score}'} for f,value in definitions]
    document['nodes'][0]['children']=[variable('customer_name','"Chris"'),variable('score','42'),node('action','create_ticket',config=config)]
    assert save(client,path,document,headers).status_code==200
    response=event(client,path,headers).json();ticket=client.get(base(path)+'/tickets/'+response['last_ticket']['id']).json()['ticket']
    assert [ticket['custom_fields'][f['key']]['value'] for f,_ in definitions]==[42,False,'2026-11-13','A customer note']


@pytest.mark.parametrize('change',[{'tag_ids':[str(uuid.uuid4())]},{'tag_group_ids':[str(uuid.uuid4())]},{'assignee_type':'user','assignee_id':str(uuid.uuid4())},{'email_domain_id':str(uuid.uuid4())},{'custom_fields':[{'field_id':str(uuid.uuid4()),'value':'foreign'}]}])
def test_missing_or_foreign_ticket_references_are_rejected(client,workflow,change):
    path,document,headers=workflow;config=ticket_config();config.update(change);document['nodes'][0]['children']=[node('action','create_ticket',config=config)]
    assert save(client,path,document,headers).status_code==422


def test_ticket_search_is_literal_and_workspace_scoped(client,workflow):
    path,headers,response=start_ticket(client,workflow);url=base(path)+'/tickets'
    assert len(client.get(url,params={'q':'Chris'}).json()['tickets'])==1
    assert client.get(url,params={'q':'%'}).json()['tickets']==[]
    assert len(client.get(url,params={'q':'PTX-000001'}).json()['tickets'])==1
    assert client.get(url,params={'status':'closed'}).json()['tickets']==[]


def test_snapshot_is_independent_of_later_chat_field_edits(client,workflow,app):
    path,document,headers=workflow;_,field=create_field(client,path,headers,{**field_body(),'default_value':'accounts'})
    path,headers,response=start_ticket(client,workflow)
    with app.state.session_factory() as db:
        row=db.get(AgentConversation,uuid.UUID(response.json()['conversation_id']));row.custom_fields={};db.commit()
    detail=client.get(base(path)+'/tickets/'+response.json()['last_ticket']['id']).json()
    assert detail['ticket']['custom_fields'][field['key']]['value']=='accounts'


def test_custom_field_option_edits_cannot_break_static_ticket_override(client,workflow):
    path,document,headers=workflow;field_path,field=create_field(client,path,headers)
    config=ticket_config();config['custom_fields']=[{'field_id':field['id'],'value':'mobile_banking'}]
    document['nodes'][0]['children']=[node('action','create_ticket',config=config)];assert save(client,path,document,headers).status_code==200
    updated={**field_body(),'expected_revision':field['revision'],'options':[{'name':'Banking','options':[{'value':'accounts','label':'Accounts'}]}]}
    assert client.post(field_path+'/'+field['id'],json=updated,headers=headers).status_code==409


def test_empty_team_still_creates_ticket_and_customer_email(client,workflow,app):
    from test_live_agent_handoff import support
    path,_,headers=workflow;_,team=support(client,path,headers,[])
    domain,template=mail_settings(client,workflow,app);config=ticket_config();config.update(assignee_type='team',assignee_id=team['id'],email_domain_id=domain['id'],customer_template_id=template['id'],assignee_template_id=template['id'])
    path,headers,response=start_ticket(client,workflow,config);detail=client.get(base(path)+'/tickets/'+response.json()['last_ticket']['id']).json()
    assert detail['ticket']['assignee_type']=='team' and len(detail['emails'])==1 and detail['emails'][0]['status']=='sent'
    assert any('no current members' in note['content'] for note in detail['notes'])


def test_ticket_and_outbox_are_committed_before_smtp(client,workflow,app):
    domain,template=mail_settings(client,workflow,app);observed=[]
    class InspectDelivery:
        def send_ticket(self,row):
            with app.state.session_factory() as db:
                stored=db.get(TicketEmail,row.id);ticket=db.get(SupportTicket,row.ticket_id);chat=db.get(AgentConversation,ticket.conversation_id)
                assert stored.status=='sending' and chat.workflow_state['last_ticket']['id']==str(ticket.id)
                observed.append(ticket.reference)
    app.state.ticket_mailer=InspectDelivery();config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id'])
    path,headers,response=start_ticket(client,workflow,config);assert observed==['PTX-000001']


def test_lost_initial_response_restores_same_ticket_and_conversation(client,workflow,app):
    domain,template=mail_settings(client,workflow,app);config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=template['id'])
    identifier=str(uuid.uuid4());path,headers,first=start_ticket(client,workflow,config,identifier=identifier)
    again=event(client,path,headers,client_event_id=identifier)
    assert again.status_code==200 and again.json()['conversation_id']==first.json()['conversation_id'],again.text
    assert again.json()['last_ticket']['id']==first.json()['last_ticket']['id']
    assert sum(message['content']=='Ticket PTX-000001 was logged.' for message in again.json()['messages'])==1
    assert len(client.get(base(path)+'/tickets').json()['tickets'])==1 and len(app.state.ticket_mailer.messages)==1


def test_email_template_can_use_overridden_ticket_fields(client,workflow,app):
    domain,template=mail_settings(client,workflow,app);path,_,headers=workflow
    _,field=create_field(client,path,headers,{**field_body(),'default_value':'accounts'})
    receipt=create_setting(client,path,headers,'email-templates',{'name':'Field receipt','content':'<p>Category: {ticket.custom_fields.enquiry_type.label}</p>'})
    config=ticket_config();config.update(email_domain_id=domain['id'],customer_template_id=receipt['id'],custom_fields=[{'field_id':field['id'],'value':'mobile_banking'}])
    start_ticket(client,workflow,config)
    assert app.state.ticket_mailer.messages[0]['html']=='<p>Category: Mobile banking</p>'


def test_custom_ticket_view_and_manage_are_independent_in_api_and_ui_capabilities(client,workflow,app):
    from test_workspace_settings import custom_actor
    from app.models import WorkspaceRole
    path,headers,response=start_ticket(client,workflow)
    root=base(path);identifier=response.json()['last_ticket']['id']
    user_id,role_id=custom_actor(app,root.split('/')[3],['view_tickets_all'])
    assert len(client.get(root+'/tickets').json()['tickets'])==1
    detail=client.get(root+'/tickets/'+identifier)
    assert detail.status_code==200 and detail.json()['can_manage'] is False
    assert client.post(root+'/tickets/'+identifier+'/notes',json={'content':'Denied'},headers=headers).status_code==404
    with app.state.session_factory() as db:
        row=db.get(WorkspaceRole,role_id);row.permissions=['view_tickets_all','manage_tickets_all'];db.commit()
    assert client.get(root+'/tickets/'+identifier).json()['can_manage'] is True
    assert client.post(root+'/tickets/'+identifier+'/notes',json={'content':'Allowed'},headers=headers).status_code==201
