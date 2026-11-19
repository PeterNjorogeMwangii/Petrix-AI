"""0020 is additive; old workflow tickets and mail retain their identities/history."""
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_0020_backfills_ticket_space_teams_and_sender_without_rewriting_history(database_engine):
    schema='ticket_workspace_'+uuid.uuid4().hex
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with database_engine.connect() as c:
        original=c.execute(text('SELECT current_schema()')).scalar();c.execute(text('CREATE SCHEMA "'+schema+'"'));c.commit()
        try:
            c.execute(text('SET search_path TO "'+schema+'"'));c.commit();config.attributes['connection']=c
            with c.begin():command.upgrade(config,'0019')
            ws,space,ticket,team,domain,email,note=[uuid.uuid4() for _ in range(7)]
            c.execute(text("INSERT INTO workspaces(id,name,created_at) VALUES(:id,'Existing',now())"),{'id':ws})
            c.execute(text("INSERT INTO teamspaces(id,workspace_id,name,name_key,is_default,revision) VALUES(:id,:ws,'Default','default',true,1)"),{'id':space,'ws':ws})
            c.execute(text("INSERT INTO ticket_email_domains(id,workspace_id,domain,from_name,local_part,verification_method,challenge,enabled,revision,archived,created_at,updated_at) VALUES(:id,:ws,'mail.example.com','Support','support','txt','existing',true,2,false,now(),now())"),{'id':domain,'ws':ws})
            c.execute(text("INSERT INTO support_tickets(id,workspace_id,action_node_id,execution_id,number,reference,agent_name,subject,description,full_name,email,phone,status,assignee_type,assignee_id,assignee_name,custom_fields,tags,revision,created_at,updated_at) VALUES(:id,:ws,:action,:execution,71,'PTX-000071','Existing AI','Existing subject','Existing description','Existing Customer','customer@example.com','','in_progress','team',:team,'Existing team','{\"case\":{\"value\":\"KEEP\"}}','[]',9,now(),now())"),{'id':ticket,'ws':ws,'action':uuid.uuid4(),'execution':uuid.uuid4(),'team':team})
            c.execute(text("INSERT INTO ticket_notes(id,ticket_id,author_name,kind,content,created_at) VALUES(:id,:ticket,'Existing agent','note','PRIVATE KEEP',now())"),{'id':note,'ticket':ticket})
            c.execute(text("INSERT INTO ticket_emails(id,ticket_id,domain_id,recipient_type,recipient,sender,sender_name,subject,body_html,body_text,message_id,status,attempts,error,revision,created_at,updated_at,sent_at) VALUES(:id,:ticket,:domain,'customer','customer@example.com','support@mail.example.com','Support','Existing email','<p>KEEP</p>','KEEP','<old@mail.example.com>','sent',1,'',2,now(),now(),now())"),{'id':email,'ticket':ticket,'domain':domain});c.commit()
            with c.begin():command.upgrade(config,'head')
            with c.begin():command.upgrade(config,'head')
            row=c.execute(text('SELECT id,teamspace_id,assigned_team_ids,sender_domain_id,status,revision,custom_fields FROM support_tickets')).one()
            assert tuple(row)==(ticket,space,[str(team)],domain,'in_progress',9,{'case':{'value':'KEEP'}})
            assert c.execute(text('SELECT id,content,color,revision,updated_at=created_at FROM ticket_notes')).one()==(note,'PRIVATE KEEP','#fff0b3',1,True)
            assert c.execute(text('SELECT id,body_text,message_id,status,cc_recipients,delivery_status FROM ticket_emails')).one()==(email,'KEEP','<old@mail.example.com>','sent',[],'')
            assert c.execute(text('SELECT count(*) FROM ticket_inbound_emails')).scalar()==0;c.commit()
            with c.begin():command.downgrade(config,'0019')
            assert c.execute(text('SELECT id,status,revision,custom_fields FROM support_tickets')).one()==(ticket,'in_progress',9,{'case':{'value':'KEEP'}})
            assert c.execute(text('SELECT id,content FROM ticket_notes')).one()==(note,'PRIVATE KEEP');c.commit()
            with c.begin():command.upgrade(config,'head')
            assert c.execute(text('SELECT count(*) FROM ticket_emails')).scalar()==1;c.commit()
        finally:
            c.rollback();c.execute(text('SET search_path TO "'+original+'"'));c.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));c.commit()
