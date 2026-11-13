"""Containers are not workflow steps; their child steps keep ordinary UUIDs."""
PATH_ACTIONS = {"branch", "send_api", "sql_query", "llm_processing"}


def path_ids(node):
    config = node["config"]
    if node["type"] == "branch":
        return [condition["id"] for condition in config["conditions"]] + [config["else_path_id"]]
    if node["type"] in {"send_api", "sql_query", "llm_processing"}:
        return [config[outcome + "_path_id"] for outcome in ("success", "error", "timeout")]
    return []
