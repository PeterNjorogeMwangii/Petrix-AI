"""Bounded workflow interpreter with explicit handlers and injectable API calls."""
from copy import deepcopy
import uuid

from fastapi import HTTPException
from app.workflows.actions.ask_survey import handler as survey
from app.workflows.actions.branch.handler import BranchSelect
from app.workflows.actions.send_api.handler import APIOutcome
from app.workflows.actions.sql_query.handler import QueryOutcome
from app.workflows.actions.jump.handler import JumpTo
from app.workflows.actions.create_ticket.handler import TicketCreated
from app.workflows.actions.collect_feedback.handler import FeedbackRequest
from app.workflows.actions.close_chat.handler import CloseChat
from app.workflows.actions.llm_processing.handler import LLMOutcome
from app.workflows.actions.auto_fill_field.handler import AutoFillOutcome
from app.workflows.actions.auto_team_match.handler import TeamMatchOutcome
from app.workflows.actions.send_graph.handler import GraphSkipped
from app.workflows.actions.ai_agent_handoff.handler import AIAgentHandoff
from app.workflows.actions.external_handoff.handler import ExternalHandoff
from app.workflows.actions.live_agent_handoff.handler import HandoffWait, HandoffUnavailable
from app.workflows.post_close import POST_CLOSE_ACTIONS, PostCloseFlow
from app.workflows.actions.registry import ACTIONS, handler_for
from app.workflows.chat_script import ScriptError
from app.workflows.context import ActionContext
from app.workflows.matching.fuzzy import normalize, similarity


def choose_trigger(nodes, event, agent, language):
    fallback, best, best_score = None, None, -1.0
    for node in nodes:
        if not node["enabled"] or node["kind"] != "trigger":
            continue
        if node["type"] == "fallback":
            fallback = node
            continue
        if node["type"] != event["event"]:
            continue
        score = 1.0
        if node["type"] == "message_received":
            config = node["config"]
            translations = config.get("examples", {})
            examples = translations.get(language) or translations.get(agent.default_language) or []
            score = max((similarity(event["content"], example) for example in examples), default=0)
            if config.get("match_mode") == "exact":
                score = 1.0 if any(normalize(event["content"]) == normalize(example) for example in examples) else 0.0
            if score < (1.0 if config.get("match_mode") == "exact" else config.get("threshold", 0.65)):
                continue
        if node["type"] == "attachment_received" and event["attachment"]["media_type"] not in node["config"]["attachment_types"]:
            continue
        if score > best_score:
            best, best_score = node, score
    # Permanent system events have their own top-level triggers, never a fallback.
    return best or (fallback if event["event"] in {"message_received", "attachment_received"} else None)


def execute(document, agent, state, event, matching=None, api_client=None, sql_client=None, attachment_store=None, continuation=None, llm_client=None, auto_fill_client=None, team_match_client=None, handoff_client=None, external_handoff_client=None, ai_handoff_client=None, ticket_client=None, resume_mode="post_close"):
    index, entries, enabled = {}, {}, {}

    def collect(nodes, continuation=None, ancestors_enabled=True):
        continuation = continuation or []
        for position, node in enumerate(nodes):
            index[node["id"]] = node
            enabled[node["id"]] = ancestors_enabled and node["enabled"]
            if node["kind"] == "trigger":
                # Enter this trigger without matching the current input again.
                # Its sibling triggers remain alternatives, never continuations.
                entries[node["id"]] = continuation + [[child["id"] for child in node["children"]]]
                child_continuation = continuation
            else:
                # Resume at the action, including its following siblings, and
                # retain only the destination's enclosing action continuations.
                entries[node["id"]] = continuation + [[sibling["id"] for sibling in nodes[position:]]]
                child_continuation = continuation + [[sibling["id"] for sibling in nodes[position + 1:]]]
            collect(node["children"], child_continuation, enabled[node["id"]])
            for path in node.get("paths", []):
                collect(path["children"], child_continuation, enabled[node["id"]])

    collect(document.nodes)
    state = deepcopy(state)
    resuming_feedback = continuation is not None
    closing = (event["event"] == "chat_closed" or resuming_feedback) and resume_mode != "handoff"
    close_graph = PostCloseFlow(document.nodes)
    if closing:
        state["closed"] = True
        root = next((value for value in document.nodes if value["type"] == "chat_closed"), None)
        issue = close_graph.issue(root["id"]) if root else "The permanent Chat Closed trigger is missing."
        if issue: raise HTTPException(422, issue)
    language = event.get("language") or state.get("language") or agent.default_language
    if language not in agent.languages:
        language = agent.default_language
    state["language"] = language
    context = ActionContext(agent=agent, language=language, event=event, state=state, api_client=api_client, sql_client=sql_client, attachment_store=attachment_store, llm_client=llm_client, auto_fill_client=auto_fill_client, team_match_client=team_match_client, handoff_client=handoff_client, external_handoff_client=external_handoff_client, ai_handoff_client=ai_handoff_client, ticket_client=ticket_client)
    same_revision = state.get("workflow_revision") == document.revision
    stack = deepcopy(continuation) if resuming_feedback else [list(frame) for frame in state.get("stack", [])] if same_revision else []
    trace, output = [], []
    trigger = None
    blocked = False
    survey_resumed = False

    def finish(matched):
        state.update({"stack": stack, "language": context.language, "workflow_revision": document.revision})
        return state, output, trace, matched

    def choose(nodes, scope):
        nonlocal blocked
        if matching is None:
            return choose_trigger(nodes, event, agent, context.language)
        decision = matching.choose(nodes, event, context.language, scope)
        blocked = decision.needs_clarification
        return decision.node

    system_event = closing or event["event"] in {"chat_started", "chat_timeout", "ai_timeout"}
    if not same_revision or system_event:
        state.pop("survey", None)
    if state.get("survey"):
        node = index.get(state["survey"]["node_id"])
        if not node or node["type"] != "ask_survey" or not enabled.get(node["id"]):
            raise HTTPException(422, "This survey is no longer available. Restart the tester.")
        if event["event"] not in {"message_received", "attachment_received"}:
            return finish(True)
        token = event.get("survey_answer_token")
        if token is not None and token != state["survey"]["answer_token"]:
            raise HTTPException(409, "This answer belongs to an earlier survey question. Use the current question.")
        try:
            field = survey.active_field(node, state)
            result, outcome = survey.answer(node, context)
        except ScriptError as error:
            raise HTTPException(422, "Step '{}': {}".format(node["name"], error)) from None
        trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"],
            "type": "ask_survey", "outcome": outcome, "field_id": field["id"], "variable_name": field["variable_name"]})
        if result is not None:
            output.append(result)
            return finish(True)
        survey_resumed = True

    pending_root = state.get("intent", {}).get("needs_clarification") and state.get("intent", {}).get("scope") == "root"
    if not survey_resumed and stack and not pending_root and not system_event:
        # The top frame contains the sibling trigger alternatives where execution paused.
        trigger = choose([index[key] for key in stack[-1] if key in index], "nested")
        if trigger:
            stack.pop()
    if not resuming_feedback and not survey_resumed and not trigger and not blocked:
        trigger = choose(document.nodes, "root")
        if trigger:
            stack = []
    if matching is not None and not survey_resumed and not resuming_feedback:
        state["intent"] = matching.report
    if blocked:
        output.append({"content": matching.last.clarification_question, "quick_replies": [], "source": "clarification", "node_id": None})
        trace.append({"node_id": None, "node_number": None, "name": "Clarification", "type": "clarification", "outcome": "requested"})
    if trigger:
        trace.append({"node_id": trigger["id"], "node_number": trigger["number"], "name": trigger["name"], "type": trigger["type"], "outcome": "matched"})
        stack.append([node["id"] for node in trigger["children"]])
    if trigger or survey_resumed or resuming_feedback:
        steps = 0
        jumps = set()
        while stack:
            steps += 1
            if steps > 1100:
                raise HTTPException(422, "Workflow execution limit exceeded. Check Jump actions for a loop without a waiting trigger.")
            if not stack[-1]:
                stack.pop()
                continue
            node = index.get(stack[-1][0])
            if not node or not node["enabled"]:
                stack[-1].pop(0)
                continue
            if node["kind"] == "trigger":
                if closing: raise HTTPException(422, "Chat Closed automation cannot wait for an incoming trigger.")
                break
            stack[-1].pop(0)
            try:
                if closing and node["type"] not in POST_CLOSE_ACTIONS:
                    raise ScriptError("This action is not available after chat closure.")
                if not closing and close_graph.owner.get(node["id"]) == "chat_closed":
                    raise ScriptError("Chat Closed steps only run during chat closure automation.")
                if node["type"] not in ACTIONS or not ACTIONS[node["type"]]["available"]:
                    raise ScriptError("This action is not ready to execute yet.")
                previous_language = context.language
                context.current_node_id=node["id"]
                result = handler_for(node["type"])(node["config"], context)
                if isinstance(result, TicketCreated):
                    trace.append({"node_id":node["id"],"node_number":node["number"],"name":node["name"],"type":"create_ticket","outcome":"created","ticket_id":result.id,"reference":result.reference})
                    stack.append([child["id"] for child in node["children"]]);continue
                if isinstance(result, HandoffUnavailable):
                    path = next((p for p in node['paths'] if p['id']==result.path_id), None)
                    if path is None: raise ScriptError('The unavailable handoff path is missing.')
                    trace.append({'node_id':node['id'],'node_number':node['number'],'name':node['name'],
                        'type':'live_agent_handoff','outcome':'unavailable','reason':result.reason})
                    stack.append([child['id'] for child in path['children']]);continue
                if isinstance(result, AIAgentHandoff):
                    state['ai_handoff_request']={'connection_id':result.connection_id,'node_id':node['id'],'node_number':node['number']}
                    trace.append({'node_id':node['id'],'node_number':node['number'],'name':node['name'],'type':'ai_agent_handoff','outcome':'transferring'})
                    state.pop('survey',None);stack=[]
                    return finish(True)
                if isinstance(result, ExternalHandoff):
                    state['external_handoff_request'] = {'platform': result.platform, 'node_id': node['id'], 'node_number': node['number']}
                    trace.append({'node_id':node['id'],'node_number':node['number'],'name':node['name'],'type':'external_handoff','outcome':'transfer_requested'})
                    state.pop('survey', None); stack=[]
                    return finish(True)
                if isinstance(result, HandoffWait):
                    path = next((p for p in node['paths'] if p['id']==node['config']['closed_path_id']), None)
                    if path is None: raise ScriptError('The closed handoff path is missing.')
                    state['handoff_request']={'node_id':node['id'],'node_number':node['number'],
                        'workflow_revision':document.revision,'nodes':deepcopy(document.nodes),
                        'continuation':deepcopy(stack)+[[child['id'] for child in path['children']]],
                        'routing':result.routing,'eligible_ids':result.eligible_ids,'assigned_id':result.assigned_id,
                        'priority':result.priority,'priority_type':result.priority_type}
                    trace.append({'node_id':node['id'],'node_number':node['number'],'name':node['name'],
                        'type':'live_agent_handoff','outcome':'assigned' if result.assigned_id else 'queued'})
                    state.pop('survey',None);stack=[]
                    return finish(True)
                if isinstance(result, GraphSkipped):
                    trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"],
                        "type": "send_graph", "outcome": "skipped", "reason": result.reason})
                    stack.append([child["id"] for child in node["children"]])
                    continue
                if isinstance(result, CloseChat):
                    already_closed = bool(state.get("closed"))
                    trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"],
                        "type": "close_chat", "outcome": "already_closed" if already_closed else "closed"})
                    if already_closed:
                        stack.append([child["id"] for child in node["children"]])
                        continue
                    state["closed"] = True
                    state["close_requested"] = {"node_id": node["id"], "node_number": node["number"]}
                    state.pop("survey", None); stack = []
                    return finish(True)
                if isinstance(result, FeedbackRequest):
                    stack.append([child["id"] for child in node["children"]])
                    context.state["feedback_request"] = {**result.payload, "token": str(uuid.uuid4()),
                        "node_id": node["id"], "node_number": node["number"], "language": context.language,
                        "workflow_revision": document.revision, "continuation": deepcopy(stack)}
                    trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"], "type": "collect_feedback", "outcome": "rating_requested" if result.payload["rating_enabled"] else "survey_requested"})
                    stack = []
                    return finish(True)
                if isinstance(result, (BranchSelect, APIOutcome, QueryOutcome, LLMOutcome, AutoFillOutcome, TeamMatchOutcome)):
                    path = next((path for path in node.get("paths", []) if path["id"] == result.path_id), None)
                    if path is None:
                        raise ScriptError("The selected action path is missing. Save the action settings again.")
                    if isinstance(result, BranchSelect):
                        detail = {"outcome": "branched", "condition_name": result.condition_name}
                    elif isinstance(result, TeamMatchOutcome):
                        detail = {"outcome": result.outcome, "error": result.error, "team_name": result.team_name, "match_reason": result.match_reason}
                    elif isinstance(result, AutoFillOutcome):
                        detail = {"outcome": result.outcome, "error": result.error, "model": result.model, "assigned_count": result.assigned_count, "unmatched_count": result.unmatched_count}
                    elif isinstance(result, LLMOutcome):
                        detail = {"outcome": result.outcome, "error": result.error, "model": result.model, "knowledge_sources": result.knowledge_sources}
                    elif isinstance(result, QueryOutcome):
                        detail = {"outcome": result.outcome, "row_count": result.row_count, "error": result.error}
                    else:
                        detail = {"outcome": result.outcome, "status_code": result.status_code, "error": result.error}
                    trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"],
                        "type": node["type"], "path_id": result.path_id, **detail})
                    stack.append([child["id"] for child in path["children"]])
                    continue
                if isinstance(result, survey.SurveyWait):
                    output.append(survey.question(node, context))
                    trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"],
                        "type": "ask_survey", "outcome": "waiting_for_answer"})
                    stack.append([child["id"] for child in node["children"]])
                    break
                if isinstance(result, JumpTo):
                    target = index.get(result.target_node_id)
                    if target and not closing and close_graph.owner.get(target["id"]) == "chat_closed":
                        raise ScriptError("Only Chat Closed automation can enter its steps.")
                    if not target or not enabled.get(result.target_node_id):
                        raise ScriptError("The Jump target is missing or disabled. Choose an enabled step in this agent's workflow.")
                    if node["id"] in jumps:
                        raise ScriptError("Jump loop detected. Wait for a new user event before jumping back to this step.")
                    jumps.add(node["id"])
                    trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"], "type": "jump",
                        "outcome": "jumped", "target_node_id": target["id"], "target_node_number": target["number"]})
                    stack = deepcopy(entries[result.target_node_id])
                    if target["kind"] == "trigger":
                        trace.append({"node_id": target["id"], "node_number": target["number"], "name": target["name"],
                            "type": target["type"], "outcome": "jumped_to"})
                    continue
            except ScriptError as error:
                raise HTTPException(422, "Step '{}': {}".format(node["name"], error)) from None
            if result is not None:
                output.append({**result, "node_id": node["id"], "source": node["type"]})
            detail = {"previous_language": previous_language, "target_language": context.language} if node["type"] == "switch_language" else {}
            trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"], "type": node["type"], "outcome": "executed", **detail})
            stack.append([child["id"] for child in node["children"]])
    return finish(trigger is not None or survey_resumed or resuming_feedback or blocked)
