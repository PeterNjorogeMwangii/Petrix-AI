import uuid
from copy import deepcopy
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.security import token_hash, utcnow
from app.database import get_db
from app.database_connections.service import SQLQueryService
from app.models import AgentAttachment, AgentConversation, AgentMessage, RateLimit
from app.routes.agents import access
from app.workflows import runtime, service
from app.workflows.actions.registry import ACTIONS
from app.workflows.actions.ask_survey import handler as survey
from app.workflows.actions.collect_feedback.config import DEFAULT_SUBMISSION
from app.workflows.actions.collect_feedback.submission import accept_feedback, public_feedback
from app.workflows.actions.llm_processing.service import LLMProcessingService
from app.workflows.actions.auto_fill_field.service import AutoFillService
from app.teams.service import TeamMatcher
from app.inbox import service as live
from app.external_handoff import service as external
from app.ai_network import service as ai_network
from app.ai_network import sender as ai_sender
from app.auth_service import require_csrf
from app import agent_service
from app.custom_fields import service as custom_fields
from app.workflows.chat_script import ScriptError
from app.workflows.context import ActionContext
from app.workflows.templates import render
from app.workflows import attachments
from app.workflows.assets import AttachmentStore, file_response
from app.workflows.numbering import walk
from app.workflows.post_close import POST_CLOSE_ACTIONS
from app.workflows.schemas import SaveWorkflows, TesterEvent

router = APIRouter(prefix="/api/workspaces/{workspace_id}/agents/{agent_id}", tags=["Workflows"])


@router.get("/workflows")
def get_workflows(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, agent_id=agent_id)
    return {**service.payload(service.get_document(db, agent_id)), "triggers": service.TRIGGERS, "actions": [{**action, "allowed_after_close": action["type"] in POST_CLOSE_ACTIONS, "post_close_only": action["type"] == "collect_feedback"} for action in ACTIONS.values()],
        "llm_processing": {"model": request.app.state.settings.groq_intent_model, "provider": "Groq",
            "available": request.app.state.llm_processing_client.available, "knowledge_supported": True}}


@router.post("/workflows")
def save_workflows(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: SaveWorkflows, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    return service.save(db, agent, session.user_id, body)


@router.post("/tester/events")
def tester_event(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: TesterEvent, request: Request, db: Session = Depends(get_db)):
    return process_event(workspace_id, agent_id, body, request, db)


@router.get('/tester/conversations/{conversation_id}')
def conversation_state(workspace_id:uuid.UUID,agent_id:uuid.UUID,conversation_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=access(request,db,workspace_id,agent_id=agent_id)
    conversation=db.scalar(select(AgentConversation).where(AgentConversation.id==conversation_id,
        AgentConversation.workspace_id==workspace_id,AgentConversation.agent_id==agent_id,AgentConversation.user_id==session.user_id))
    if not conversation:raise HTTPException(404,'Conversation not found.')
    outgoing=db.get(ai_network.AIOutgoingSession,conversation.id)
    if outgoing and outgoing.status!='failed':return ai_sender.refresh(db,conversation,outgoing,request)
    return snapshot_payload(db,conversation)


def snapshot_payload(db,conversation):
    document=live.frozen_document(conversation) if conversation.live_context.get('resume_active') else service.get_document(db,conversation.agent_id)
    return result_payload(document,conversation,conversation.workflow_state,live.messages(db,conversation)['messages'],
        conversation.live_context.get('last_trace',[]),None,True)


def result_payload(document, conversation, state, messages, trace, matching, matched, file_metadata=None, notice=None):
    active_node = next((node for node in walk(document.nodes) if node["id"] == state.get("survey", {}).get("node_id")), None)
    feedback = state.get("feedback_request") if state.get("closed") and not state.get("feedback_submitted") else None
    automation_error = (state.get("feedback_automation") or state.get("close_automation") or {}).get("error")
    return {"conversation_id": str(conversation.id), "messages": messages, "trace": trace, "last_ticket": state.get("last_ticket"),
        "workflow_revision": document.revision, "language": state.get("language", "en"), "closed": bool(state.get("closed")),
        "feedback_submitted": bool(state.get("feedback_submitted")), "waiting": bool(state.get("stack")) or bool(state.get("survey")),
        "variables": state.get("variables", {}), "custom_fields": conversation.custom_fields or {}, "survey": survey.status(active_node, state) if active_node else None,
        "feedback": {**public_feedback(feedback), "min_rating": 1, "max_rating": 5} if feedback else None,
        "feedback_confirmation": state.get("feedback_confirmation"), "channel": state.get("channel", "webchat"),
        "ai_handoff": state.get("ai_handoff"), "external_handoff": state.get("external_handoff"), "automation_error": automation_error, "closed_at": state.get("closed_at"), "close_source": state.get("close_source"), "csat_rating": state.get("csat_rating"),
        "attachments": file_metadata or [], "matching": matching.report if matching else None, "live_chat": live.public_status(conversation),
        "notice": notice if notice is not None else "" if messages or matched else "No workflow matched this event. Add a matching trigger or enable a fallback."}


def lock_conversation(db, conversation_id):
    return db.scalar(select(AgentConversation).where(AgentConversation.id == conversation_id)
        .with_for_update().execution_options(populate_existing=True))


def store_input(db, conversation, body, files, source_request_id=None):
    provenance={"source_request_id":str(source_request_id)} if source_request_id else {}
    if body.event == "message_received":
        db.add(AgentMessage(conversation_id=conversation.id, role="user", content=body.content,message_data=provenance))
    elif body.event == "attachment_received":
        db.add(AgentMessage(conversation_id=conversation.id, role="user",
            content="Uploaded: " + ", ".join(file["name"] for file in files) if files else "Attachment test: " + body.attachment.name,
            message_data={**provenance, "event": body.event, "attachment": body.attachment.model_dump(), "attachments": files}))


def store_output(db, conversation, output):
    messages = []
    for result in output:
        message = AgentMessage(conversation_id=conversation.id, role="assistant", content=result["content"],
            message_data={key: value for key, value in result.items() if key != "content"})
        db.add(message); db.flush()
        messages.append({"id": str(message.id), "role": message.role, "content": message.content,
            "created_at": message.created_at.isoformat(), **message.message_data})
    return messages


def process_event(workspace_id, agent_id, body, request, db, upload_payloads=None, inbox_actor=None, inbox_revision=None, network_actor=None):
    result=_process_event(workspace_id,agent_id,body,request,db,upload_payloads,inbox_actor,inbox_revision,network_actor)
    from app.workspace_settings.usage import record_actions
    from app.models import AgentConversation
    metered_conversation=db.get(AgentConversation,uuid.UUID(result['conversation_id']))
    record_actions(db,workspace_id,agent_id,metered_conversation.user_id,result,body.client_event_id or (network_actor.request_id if network_actor else None))
    if result.get('last_ticket'):
        from app.tickets.notifications import dispatch
        dispatch(db,request.app.state.ticket_mailer,conversation_id=uuid.UUID(result['conversation_id']))
    from app.teamspace.jobs import maybe_auto
    maybe_auto(db,metered_conversation,request.app.state.settings)
    if inbox_actor:
        from app.teamspace.assignment import drain
        drain(db,workspace_id)
    return result


def _process_event(workspace_id, agent_id, body, request, db, upload_payloads=None, inbox_actor=None, inbox_revision=None, network_actor=None):
    if network_actor is not None:
        if network_actor.agent_id!=agent_id or body.conversation_id not in {None,network_actor.conversation_id}:
            raise HTTPException(403,'Network session scope mismatch.')
        session=network_actor
        agent_service.workspace_for(db,workspace_id,session.user_id,manage=True)
        agent=agent_service.agent_for(db,workspace_id,agent_id)
    elif inbox_actor is None:
        session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id, lock=False)
    else:
        if body.event!='chat_closed' or not body.conversation_id:raise HTTPException(400,'Inbox may only close an existing live chat.')
        session=inbox_actor;require_csrf(request,session)
        agent_service.workspace_for(db,workspace_id,session.user_id)
        agent=agent_service.agent_for(db,workspace_id,agent_id)
    now = utcnow()
    key = token_hash("workflow-tester:{}:{}".format(session.user_id, int(now.timestamp()) // 60))
    statement = insert(RateLimit).values(key=key, count=1, expires_at=now + timedelta(minutes=2))
    count = db.execute(statement.on_conflict_do_update(index_elements=[RateLimit.key], set_={"count": RateLimit.count + 1}).returning(RateLimit.count)).scalar_one()
    db.commit()
    if count > 60:
        raise HTTPException(429, "Too many tester events. Wait a minute and try again.")
    if body.language and body.language not in agent.languages and not body.conversation_id and not network_actor:
        raise HTTPException(422, "Choose a language configured for this agent.")
    document = service.get_document(db, agent_id)
    recovered_ticket_start=False
    selected_conversation=network_actor.conversation_id if network_actor else body.conversation_id
    if not selected_conversation and not network_actor and body.event=='chat_started' and body.client_event_id:
        selected_conversation=db.scalar(select(AgentConversation.id).join(ai_network.AIOutgoingSession,ai_network.AIOutgoingSession.conversation_id==AgentConversation.id).where(
            AgentConversation.workspace_id==workspace_id,AgentConversation.agent_id==agent_id,AgentConversation.user_id==session.user_id,ai_network.AIOutgoingSession.transfer_id==body.client_event_id))
        if not selected_conversation:
            from app.models import SupportTicket
            selected_conversation=db.scalar(select(AgentConversation.id).join(SupportTicket,SupportTicket.conversation_id==AgentConversation.id).where(
                AgentConversation.workspace_id==workspace_id,AgentConversation.agent_id==agent_id,AgentConversation.user_id==session.user_id,SupportTicket.execution_id==body.client_event_id).limit(1))
            recovered_ticket_start=bool(selected_conversation)
    if selected_conversation:
        conversation = db.scalar(select(AgentConversation).where(AgentConversation.id == selected_conversation,
            AgentConversation.agent_id == agent_id, AgentConversation.workspace_id == workspace_id,
            *([] if inbox_actor else [AgentConversation.user_id == session.user_id])).with_for_update().execution_options(populate_existing=True))
        if not conversation:
            raise HTTPException(404, "Conversation not found.")
    else:
        conversation = AgentConversation(workspace_id=workspace_id, agent_id=agent_id, user_id=session.user_id,
            agent_name=agent.name, workflow_state={})
        db.add(conversation)
        db.flush()
    from app.teamspace.records import person_for,check_ban,check_resolution
    person_for(db,conversation,network_actor is not None)
    if not inbox_actor:check_ban(db,conversation,network_actor is not None)
    if body.event=='chat_closed':check_resolution(db,workspace_id,'chats',conversation)
    outgoing=db.get(ai_network.AIOutgoingSession,conversation.id)
    if outgoing and outgoing.status!='failed' and inbox_actor is None:
        return ai_sender.forward_event(db,conversation,outgoing,body,request,upload_payloads,network_actor)
    if recovered_ticket_start:return snapshot_payload(db,conversation)
    if body.language and body.language not in agent.languages:
        raise HTTPException(422,'Choose a language configured for this agent.')
    external_binding=db.get(external.ExternalConversation,conversation.id)
    if external_binding and external_binding.status in external.BLOCKED:
        raise HTTPException(409, 'This conversation has left Petrix AI processing. Continue in the external platform; unconfirmed transfers require review.')
    if inbox_actor:
        live.require_manage(db,conversation,session.user_id)
        live.require_assignee(conversation,session.user_id,inbox_revision)
    elif conversation.live_status in {'pending','active'}:
        if body.event!='message_received':raise HTTPException(409,'This conversation is with live support. Close it from Inbox.')
        if body.language and body.language!=conversation.workflow_state.get('language'):raise HTTPException(409,'The live chat keeps the language selected at handoff.')
        store_input(db,conversation,body,[],network_actor.request_id if network_actor else body.client_event_id);conversation.live_updated_at=now;db.commit()
        return result_payload(document,conversation,conversation.workflow_state,[],[],None,True,notice='Your message was sent to live support.')
    if conversation.live_context.get('resume_active') and body.event in {'message_received','attachment_received'}:
        document=live.frozen_document(conversation)
    previous = deepcopy(conversation.workflow_state or {})
    previous["custom_fields"] = custom_fields.with_defaults(db, workspace_id, conversation)
    if body.channel and previous.get("channel") and body.channel != previous["channel"]:
        raise HTTPException(409, "Restart the tester before changing its channel preview.")
    previous.setdefault("channel", body.channel or "webchat")
    if previous.get("closed") and body.event != "collect_feedback":
        raise HTTPException(409, "This chat is closed. Restart the tester to begin a new conversation.")
    pending_feedback = None
    if body.event == "collect_feedback":
        if not previous.get("closed"):
            raise HTTPException(409, "Close the chat before submitting CSAT feedback.")
        if previous.get("feedback_submitted"):
            raise HTTPException(409, "Feedback has already been submitted for this chat.")
        pending_feedback = previous.get("feedback_request")
        if not pending_feedback:
            raise HTTPException(409, "Feedback has not been requested for this chat.")
        if str(body.feedback_token or "") != pending_feedback.get("token"):
            raise HTTPException(409, "Use the rating request issued for this conversation.")
        previous = accept_feedback(pending_feedback, body.rating, {str(key): value for key, value in body.feedback_answers.items()}, agent, previous)
    lifecycle_key = "close_automation" if body.event == "chat_closed" else "feedback_automation" if body.event == "collect_feedback" else None
    if lifecycle_key:
        if body.event == "chat_closed":
            if inbox_actor:
                conversation.live_status='closed';conversation.live_revision+=1;conversation.live_updated_at=now
                conversation.live_context={**conversation.live_context,'return_pending':True,'return_status':'waiting_automation'}
                live.audit(db,conversation,session.user_id,'closed')
            previous.update({"closed": True, "closed_at": now.isoformat(), "closed_by": str(session.user_id),
                "close_source": "live_agent" if inbox_actor else "tester", "stack": []})
            previous.pop("survey", None)
        else:
            previous.update({"feedback_submitted": True, "csat_rating": body.rating, "feedback_submitted_at": now.isoformat()})
            previous.pop("feedback_request", None)
        previous[lifecycle_key] = {"status": "running"}
        conversation.custom_fields = deepcopy(previous.get("custom_fields", conversation.custom_fields or {}))
        conversation.workflow_state = deepcopy(previous); db.commit()
        conversation = lock_conversation(db, conversation.id)
        previous = deepcopy(conversation.workflow_state)
    pending = previous.get("survey")
    if body.event in {"message_received", "attachment_received"}:
        if body.survey_answer_token and previous.get("workflow_revision") != document.revision:
            raise HTTPException(409, "Workflows changed during this survey. Restart the tester before answering.")
        if pending and previous.get("workflow_revision") == document.revision:
            if str(body.survey_answer_token or "") != pending.get("answer_token"):
                raise HTTPException(409, "This answer belongs to an earlier survey question. Answer the current question.")
        elif body.survey_answer_token:
            raise HTTPException(409, "There is no active survey question for this answer.")
    event_context = body.model_dump(mode="json")
    event_context["conversation_id"] = str(conversation.id)
    from app.tickets.service import TicketService
    ticket_service=TicketService(db,workspace_id,session.user_id,conversation,body.client_event_id or (network_actor.request_id if network_actor and network_actor.request_id else uuid.uuid4()))
    if pending_feedback: event_context["language"] = pending_feedback.get("language") or previous.get("language")
    file_metadata = []
    if upload_payloads is not None:
        survey_node = next((node for node in walk(document.nodes) if node["id"] == (pending or {}).get("node_id")), None)
        if not survey_node or previous.get("workflow_revision") != document.revision:
            raise HTTPException(409, "There is no active survey upload question. Restart the tester if the workflow changed.")
        field = survey.active_field(survey_node, previous)
        if field["answer_type"] not in {"attachment", "attachments"}:
            raise HTTPException(409, "The current survey question expects a typed answer.")
        if field["answer_type"] == "attachment" and len(upload_payloads) != 1:
            raise HTTPException(422, "This survey question accepts exactly one file.")
        for payload in upload_payloads:
            file = AgentAttachment(conversation_id=conversation.id, **payload)
            db.add(file); db.flush()
            file_metadata.append(attachments.metadata(file, workspace_id, agent_id))
        event_context["attachments"] = file_metadata
    history = []
    if body.event == "message_received":
        recent = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(6)).all()
        history = [{"role": item.role, "content": item.content} for item in reversed(recent)]

    def reserve_llm_request():
        llm_key = token_hash("tester:{}:{}".format(session.user_id, int(now.timestamp()) // 60))
        limit = insert(RateLimit).values(key=llm_key, count=1, expires_at=now + timedelta(minutes=2))
        llm_count = db.execute(limit.on_conflict_do_update(index_elements=[RateLimit.key],
            set_={"count": RateLimit.count + 1}).returning(RateLimit.count)).scalar_one()
        if llm_count > request.app.state.settings.llm_tester_requests_per_minute:
            raise HTTPException(429, "Too many model requests. Wait a minute and try again.")

    matching = request.app.state.intent_matcher.session(document, agent, history, reserve_llm_request)
    processing = LLMProcessingService(db, agent, request.app.state.settings, request.app.state.llm_processing_client, reserve_llm_request,request.app.state.agent_moderator)

    auto_fill = AutoFillService(db, workspace_id, conversation.id, processing)
    team_match = TeamMatcher(db, workspace_id)
    handoff = live.HandoffService(db, workspace_id, conversation)
    ai_handoff=ai_network.TransferPlan(db,conversation,request.app.state.settings)
    external_handoff=external.HandoffService(db,conversation,request.app.state.settings,request.app.state.external_handoff_client)

    def execute_state(value, continuation=None):
        return runtime.execute(document, agent, value, event_context, matching,
            api_client=request.app.state.send_api_client,
            sql_client=SQLQueryService(db, workspace_id, request.app.state.settings, request.app.state.database_client),
            attachment_store=AttachmentStore(db, workspace_id, agent_id, conversation.id), continuation=continuation, llm_client=processing, auto_fill_client=auto_fill, team_match_client=team_match, handoff_client=handoff, external_handoff_client=external_handoff,ai_handoff_client=ai_handoff,ticket_client=ticket_service)

    committed_messages, committed_trace = [], []
    input_saved = False
    handoff_returned = False
    try:
        if pending_feedback:
            state = deepcopy(previous)
            context = ActionContext(agent=agent, language=event_context["language"], event=event_context, state=state)
            try: state["feedback_confirmation"] = render(pending_feedback.get("submission_template", DEFAULT_SUBMISSION), context)
            except ScriptError as error: raise HTTPException(422, str(error)) from None
            output, trace = [], []
            if "continuation" in pending_feedback:
                if pending_feedback.get("workflow_revision") != document.revision:
                    raise HTTPException(422, "Feedback saved. Workflows changed after this request; its remaining actions were not run. Restart to test the updated flow.")
                state, output, trace, _ = execute_state(state, pending_feedback["continuation"])
                state["close_automation"] = {"status": "completed"}
            state, followup, followup_trace, matched = execute_state(state)
            output.extend(followup); trace.extend(followup_trace)
            trace.insert(0, {"node_id": pending_feedback.get("node_id"), "node_number": pending_feedback.get("node_number"),
                "name": "Feedback submitted", "type": "collect_feedback", "outcome": "feedback_saved"})
        else:
            state, output, trace, matched = execute_state(previous)
        if conversation.live_context.get('return_pending') and not state.get('feedback_request'):
            # Consume the return once, durably, before executing potentially external actions.
            committed_messages += store_output(db,conversation,output);output=[];committed_trace += trace
            conversation.workflow_state=deepcopy(state);conversation.custom_fields=deepcopy(state.get('custom_fields',conversation.custom_fields or {}))
            conversation.live_context={**conversation.live_context,'return_pending':False,'return_status':'running','resume_active':True}
            db.commit();conversation=lock_conversation(db,conversation.id)
            frozen=live.frozen_document(conversation);return_state=deepcopy(state);return_state['closed']=False;return_state['stack']=[]
            return_event={'event':'handoff_return','conversation_id':str(conversation.id),'language':state.get('language',agent.default_language)}
            return_matching=request.app.state.intent_matcher.session(frozen,agent)
            handoff_returned=True
            state,output,return_trace,_=runtime.execute(frozen,agent,return_state,return_event,return_matching,
                api_client=request.app.state.send_api_client,
                sql_client=SQLQueryService(db,workspace_id,request.app.state.settings,request.app.state.database_client),
                attachment_store=AttachmentStore(db,workspace_id,agent_id,conversation.id),llm_client=processing,auto_fill_client=auto_fill,
                team_match_client=team_match,handoff_client=handoff,external_handoff_client=external_handoff,ai_handoff_client=ai_handoff,ticket_client=ticket_service,continuation=conversation.live_context['continuation'],resume_mode='handoff')
            trace=committed_trace+return_trace;document=frozen
            conversation.live_context={**conversation.live_context,'return_status':'returned','resume_active':bool(state.get('stack') or state.get('survey'))}
        close_request = state.pop("close_requested", None)
        if close_request:
            check_resolution(db,workspace_id,'chats',conversation)
            # A workflow closure is durable before its post-close automation.
            # Persist the incoming turn and any earlier messages/files once.
            store_input(db, conversation, body, file_metadata,network_actor.request_id if network_actor else body.client_event_id); input_saved = True
            committed_messages += store_output(db, conversation, output)
            committed_trace = trace
            state.update({"closed": True, "closed_at": utcnow().isoformat(), "closed_by": str(session.user_id),
                "close_source": "workflow", "close_action": close_request, "close_automation": {"status": "running"}, "stack": []})
            state.pop("survey", None)
            conversation.custom_fields = deepcopy(state.get("custom_fields", conversation.custom_fields or {}))
            conversation.workflow_state = deepcopy(state); db.commit()
            conversation = lock_conversation(db, conversation.id)
            previous = deepcopy(conversation.workflow_state)
            lifecycle_key = "close_automation"
            closed_event = {"event": "chat_closed", "conversation_id": str(conversation.id), "language": previous["language"]}
            close_matching = request.app.state.intent_matcher.session(document, agent)
            state, output, close_trace, _ = runtime.execute(document, agent, previous, closed_event, close_matching,
                api_client=request.app.state.send_api_client,
                sql_client=SQLQueryService(db, workspace_id, request.app.state.settings, request.app.state.database_client),
                attachment_store=AttachmentStore(db, workspace_id, agent_id, conversation.id), llm_client=processing, auto_fill_client=auto_fill, team_match_client=team_match, handoff_client=handoff, external_handoff_client=external_handoff,ai_handoff_client=ai_handoff,ticket_client=ticket_service)
            trace = committed_trace + close_trace; matched = True
    except HTTPException as error:
        if not lifecycle_key: raise
        db.rollback()
        conversation = lock_conversation(db, conversation.id)
        state = deepcopy(conversation.workflow_state)
        state[lifecycle_key] = {"status": "failed", "error": str(error.detail)}
        if pending_feedback and "continuation" in pending_feedback: state["close_automation"] = {"status": "failed"}
        if handoff_returned or conversation.live_context.get('return_pending'):
            failed={**conversation.live_context,'return_pending':False,'return_status':'failed','resume_active':False}
            failed.pop('nodes',None);failed.pop('continuation',None);conversation.live_context=failed
        state["stack"] = []; state.pop("survey", None)
        conversation.workflow_state = state; db.commit()
        close_failed = lifecycle_key == "close_automation"
        trace = committed_trace + [{"node_id": None, "node_number": None, "name": "Chat closed" if close_failed else "Feedback received",
            "type": "chat_closed" if close_failed else body.event, "outcome": "automation_failed", "error": str(error.detail)}]
        return result_payload(document, conversation, state, committed_messages, trace, matching, True, file_metadata,
            notice="Chat closed. Post-conversation automation could not finish." if close_failed else "Feedback saved. Follow-up automation could not finish.")
    if lifecycle_key:
        state[lifecycle_key] = {"status": "waiting_feedback" if lifecycle_key == "close_automation" and state.get("feedback_request") else "completed"}
    if body.event == "message_received" and not input_saved:
        if not matched and agent.llm_enabled and request.app.state.llm_tester.available:
            reserve_llm_request()
            recent = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
                .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(20)).all()
            history = [{"role": item.role, "content": item.content} for item in reversed(recent)]
            history.append({"role": "user", "content": body.content})
            # Keep nested-trigger context if a provider fails; the whole turn rolls back.
            from app.agent_knowledge.generation import reply as knowledge_reply
            content,knowledge_meta = knowledge_reply(db,agent,request.app.state.llm_tester,history,state['language'],request.app.state.agent_moderator)
            output.append({"content": content, "quick_replies": [], "source": "llm", "node_id": None,**knowledge_meta})
            trace.append({"node_id": None, "node_number": None, "name": "Generative reply", "type": "llm", "outcome": "executed",**knowledge_meta})
    if not input_saved: store_input(db, conversation, body, file_metadata,network_actor.request_id if network_actor else body.client_event_id)
    if body.event in {"chat_closed", "chat_timeout"} and not handoff_returned:
        state["closed"] = True
    if body.event == "collect_feedback":
        state["feedback_submitted"] = True
        state["csat_rating"] = body.rating
        state.pop("feedback_request", None)
    conversation.custom_fields = deepcopy(state.get("custom_fields", conversation.custom_fields or {}))
    ai_request=state.pop('ai_handoff_request',None)
    if ai_request:
        earlier=committed_messages+store_output(db,conversation,output)
        result=ai_sender.start(db,conversation,ai_handoff,state,request,network_actor.request_id if network_actor else body.client_event_id)
        result['messages']=earlier+result['messages']
        result['trace']=trace+result.get('trace',[])
        return result
    external_request=state.pop('external_handoff_request',None)
    if external_request:
        # Terminal transfer: persist the incoming turn and already-generated output
        # before dispatch. No Chat Closed/feedback or following workflow action runs.
        messages=committed_messages+store_output(db,conversation,output)
        outcome=external_handoff.dispatch(state,session.user_id)
        state['external_handoff']=external.public_status(db,conversation)
        conversation.workflow_state=deepcopy(state);db.commit()
        trace=[{**entry,'outcome':outcome.status} if entry.get('type')=='external_handoff' else entry for entry in trace]
        return result_payload(document,conversation,state,messages,trace,matching,True,file_metadata,
            notice='Conversation transferred. Continue in '+external.PLATFORMS[external_request['platform']]+'.' if outcome.status=='transferred' else
                'The provider rejected the transfer. This chat remains with Petrix AI.' if outcome.status=='failed' else
                'Transfer confirmation is unavailable. AI processing is paused; check the external platform before reviewing the outcome.')
    live.apply_handoff(db,conversation,state)
    if conversation.live_status=='closed' and conversation.live_context.get('resume_active') and not state.get('stack') and not state.get('survey'):
        conversation.live_context={**conversation.live_context,'resume_active':False}
    if conversation.live_status!='none':
        context={**conversation.live_context,'last_trace':trace}
        if context.get('return_pending') and state.get('feedback_request'):context['return_status']='waiting_feedback'
        if not context.get('return_pending') and not context.get('resume_active') and conversation.live_status=='closed':
            context.pop('nodes',None);context.pop('continuation',None)
        conversation.live_context=context
    conversation.workflow_state = state
    messages = committed_messages + store_output(db, conversation, output)
    db.commit()
    return result_payload(document, conversation, state, messages, trace, matching, matched or body.event == "collect_feedback", file_metadata,
        notice="Feedback saved." if body.event == "collect_feedback" and not output else None)


@router.post("/tester/attachments")
async def upload_survey_answer(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request,
    conversation_id: uuid.UUID = Form(...), survey_answer_token: uuid.UUID = Form(...),
    files: List[UploadFile] = File(...), language: Optional[str] = Form(None), client_event_id:Optional[uuid.UUID]=Form(None), db: Session = Depends(get_db)):
    session, _, _, _ = access(request, db, workspace_id, mutation=True, agent_id=agent_id, lock=False)
    exists = db.scalar(select(AgentConversation.id).where(AgentConversation.id == conversation_id,
        AgentConversation.agent_id == agent_id, AgentConversation.workspace_id == workspace_id,
        ))
    if not exists:
        raise HTTPException(404, "Conversation not found.")
    payloads = await attachments.read_uploads(files)
    body = TesterEvent(event="attachment_received", conversation_id=conversation_id, language=language,
        survey_answer_token=survey_answer_token, client_event_id=client_event_id, attachment={"name": payloads[0]["file_name"], "media_type": "file"})
    return await run_in_threadpool(process_event, workspace_id, agent_id, body, request, db, upload_payloads=payloads)


@router.get("/tester/attachments/{attachment_id}")
def download_survey_answer(workspace_id: uuid.UUID, agent_id: uuid.UUID, attachment_id: uuid.UUID,
    request: Request, inline: bool = False, db: Session = Depends(get_db)):
    session, _, _, _ = access(request, db, workspace_id, agent_id=agent_id)
    file = db.scalar(select(AgentAttachment).join(AgentConversation).where(AgentAttachment.id == attachment_id,
        AgentConversation.agent_id == agent_id, AgentConversation.workspace_id == workspace_id,
        ))
    if not file or (file.conversation_id and not db.scalar(select(AgentConversation.id).where(AgentConversation.id==file.conversation_id,AgentConversation.user_id==session.user_id)) and not live.can_view(db,db.get(AgentConversation,file.conversation_id),session.user_id)):
        raise HTTPException(404, "Attachment not found.")
    return file_response(file, request, inline=inline)
