from fastapi import HTTPException

from app.workflows.numbering import walk


def validate_targets(nodes, languages):
    for node in walk(nodes):
        if node["kind"] == "action" and node["type"] == "switch_language" and node["config"]["target_language"] not in languages:
            raise HTTPException(422, "Step '{}': Target language must be configured in this agent's Agent settings. Update or remove this Switch Language action before removing its target language.".format(node["name"]))
