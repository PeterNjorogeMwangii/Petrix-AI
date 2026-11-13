"""Containers are not workflow steps; their child steps keep ordinary UUIDs."""
PATH_ACTIONS = {"branch", "send_api", "sql_query", "llm_processing", "auto_fill_field", "auto_team_match", "live_agent_handoff"}


def path_ids(node):
    config = node["config"]
    if node["type"] == "branch":
        return [condition["id"] for condition in config["conditions"]] + [config["else_path_id"]]
    if node["type"] == "live_agent_handoff":
        return [config[outcome + "_path_id"] for outcome in ("closed", "unavailable")]
    if node["type"] == "auto_team_match":
        return [config[outcome + "_path_id"] for outcome in ("match", "no_match", "error")]
    if node["type"] == "auto_fill_field":
        return [config[outcome + "_path_id"] for outcome in ("success", "error")]
    if node["type"] in {"send_api", "sql_query", "llm_processing"}:
        return [config[outcome + "_path_id"] for outcome in ("success", "error", "timeout")]
    return []
