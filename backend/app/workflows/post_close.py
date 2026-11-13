"""Post-conversation action policy, including Jump continuations."""
from fastapi import HTTPException

from app.workflows.numbering import walk
from app.workflows.paths import PATH_ACTIONS

POST_CLOSE_ACTIONS = (
    "collect_feedback", "create_ticket", "jump", "branch", "set_variable", "switch_language", "send_api",
    "llm_processing", "auto_fill_field", "auto_team_match", "sql_query", "send_graph", "close_chat",
)


class PostCloseFlow:
    def __init__(self, nodes):
        self.index, self.owner, self.after, self.trigger_entry = {}, {}, {}, {}

        def collect(values, continuation=None, root_type=None):
            for position, node in enumerate(values):
                key = node["id"]; owner = root_type or node["type"]
                self.index[key] = node; self.owner[key] = owner
                after = values[position + 1]["id"] if position + 1 < len(values) else continuation
                self.after[key] = after
                self.trigger_entry[key] = node["children"][0]["id"] if node["children"] else continuation
                collect(node["children"], continuation if node["kind"] == "trigger" else after, owner)
                for path in node.get("paths", []): collect(path["children"], after, owner)

        collect(nodes)

    def issue(self, target_id):
        target = self.index.get(target_id)
        if target is None: return "Choose an existing step."
        pending = [self.trigger_entry[target_id] if target["kind"] == "trigger" else target_id]
        seen = set()
        while pending:
            key = pending.pop()
            if key is None or key in seen: continue
            seen.add(key); node = self.index[key]
            if node["kind"] == "trigger":
                return "Step '{}': Chat Closed automation cannot wait for an incoming trigger.".format(node["name"])
            if node["type"] not in POST_CLOSE_ACTIONS:
                return "Step '{}': {} is not available after chat closure.".format(node["name"], node["type"].replace("_", " "))
            if node["type"] == "jump":
                other = self.index.get(node["config"].get("target_node_id"))
                if other is None: return "Step '{}': The Jump target is missing.".format(node["name"])
                pending.append(self.trigger_entry[other["id"]] if other["kind"] == "trigger" else other["id"])
            elif node["type"] in PATH_ACTIONS:
                pending.extend(path["children"][0]["id"] if path["children"] else self.after[key] for path in node.get("paths", []))
            else:
                pending.append(node["children"][0]["id"] if node["children"] else self.after[key])
        return ""


def validate_post_close(nodes):
    graph = PostCloseFlow(nodes)
    for root in nodes:
        if root["type"] != "chat_closed": continue
        # Policy applies to the entire saved subtree, including disabled steps.
        for node in walk(root["children"]):
            if node["kind"] != "action" or node["type"] not in POST_CLOSE_ACTIONS:
                raise HTTPException(422, "Step '{}': Only post-conversation actions can be added beneath Chat Closed.".format(node["name"]))
        issue = graph.issue(root["id"])
        if issue: raise HTTPException(422, issue)
    for node in graph.index.values():
        if node["kind"] != "action": continue
        if node["type"] == "collect_feedback" and graph.owner[node["id"]] != "chat_closed":
            raise HTTPException(422, "Step '{}': Add Collect Feedback beneath Chat Closed.".format(node["name"]))
        if node["type"] == "jump":
            target_id = node["config"]["target_node_id"]
            if graph.owner.get(target_id) == "chat_closed" and graph.owner[node["id"]] != "chat_closed":
                raise HTTPException(422, "Step '{}': Only Chat Closed automation can Jump into its steps.".format(node["name"]))
