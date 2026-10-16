import uuid

SYSTEM_TRIGGERS = ("chat_started", "chat_closed", "chat_timeout", "ai_timeout")
DEFAULT_GREETING = "Hi! I’m {_chatbot.name}. How can I assist you today?"


def node_id(agent_id, key):
    return str(uuid.uuid5(uuid.UUID(str(agent_id)), "petrix-workflow:" + key))


def default_nodes(agent_id, language="en"):
    roots = []
    triggers = (("chat_started", "Chat started"), ("chat_closed", "Chat closed"),
        ("chat_timeout", "Chat timeout"), ("ai_timeout", "AI timeout"), ("fallback", "Fallback"))
    for number, (trigger, name) in enumerate(triggers, start=1):
        root = {"id": node_id(agent_id, trigger), "number": number, "kind": "trigger", "type": trigger,
            "name": name, "description": "", "enabled": trigger != "fallback", "config": {}, "children": []}
        if trigger == "chat_started":
            root["children"].append({"id": node_id(agent_id, "greeting"), "number": len(triggers) + 1, "kind": "action", "type": "send_message",
                "name": "Send message", "description": "", "enabled": True,
                "config": {"translations": {language: {"messages": [DEFAULT_GREETING], "quick_replies": []}}}, "children": []})
        roots.append(root)
    return roots
