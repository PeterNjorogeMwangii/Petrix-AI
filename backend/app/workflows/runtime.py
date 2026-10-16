"""Bounded workflow interpreter. Action handlers are pure, individually packaged functions."""
from copy import deepcopy

from fastapi import HTTPException
from app.workflows.actions.ask_survey import handler as survey
from app.workflows.actions.jump.handler import JumpTo
from app.workflows.actions.registry import handler_for
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
    return best or (fallback if event["event"] in {"message_received", "attachment_received", "collect_feedback"} else None)


def execute(document, agent, state, event, matching=None):
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

    collect(document.nodes)
    state = deepcopy(state)
    language = event.get("language") or state.get("language") or agent.default_language
    if language not in agent.languages:
        language = agent.default_language
    state["language"] = language
    context = ActionContext(agent=agent, language=language, event=event, state=state)
    same_revision = state.get("workflow_revision") == document.revision
    stack = [list(frame) for frame in state.get("stack", [])] if same_revision else []
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
            return choose_trigger(nodes, event, agent, language)
        decision = matching.choose(nodes, event, language, scope)
        blocked = decision.needs_clarification
        return decision.node

    system_event = event["event"] in {"chat_started", "chat_closed", "chat_timeout", "ai_timeout"}
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
    if not survey_resumed and not trigger and not blocked:
        trigger = choose(document.nodes, "root")
        if trigger:
            stack = []
    if matching is not None and not survey_resumed:
        state["intent"] = matching.report
    if blocked:
        output.append({"content": matching.last.clarification_question, "quick_replies": [], "source": "clarification", "node_id": None})
        trace.append({"node_id": None, "node_number": None, "name": "Clarification", "type": "clarification", "outcome": "requested"})
    if trigger:
        trace.append({"node_id": trigger["id"], "node_number": trigger["number"], "name": trigger["name"], "type": trigger["type"], "outcome": "matched"})
        stack.append([node["id"] for node in trigger["children"]])
    if trigger or survey_resumed:
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
                break
            stack[-1].pop(0)
            try:
                result = handler_for(node["type"])(node["config"], context)
                if isinstance(result, survey.SurveyWait):
                    output.append(survey.question(node, context))
                    trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"],
                        "type": "ask_survey", "outcome": "waiting_for_answer"})
                    stack.append([child["id"] for child in node["children"]])
                    break
                if isinstance(result, JumpTo):
                    target = index.get(result.target_node_id)
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
            trace.append({"node_id": node["id"], "node_number": node["number"], "name": node["name"], "type": node["type"], "outcome": "executed"})
            stack.append([child["id"] for child in node["children"]])
    return finish(trigger is not None or survey_resumed or blocked)
