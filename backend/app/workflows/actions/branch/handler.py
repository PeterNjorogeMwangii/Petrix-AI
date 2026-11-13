from dataclasses import dataclass

from app.workflows.chat_script import ScriptError, evaluate


@dataclass(frozen=True)
class BranchSelect:
    path_id: str
    condition_name: str


def execute(config, context):
    for condition in config["conditions"]:
        try:
            matches = evaluate(condition["chat_script"], context.environment())
        except ScriptError as error:
            raise ScriptError("Condition '{}': {}".format(condition["name"], error)) from None
        if matches:
            return BranchSelect(condition["id"], condition["name"])
    return BranchSelect(config["else_path_id"], "Else")
