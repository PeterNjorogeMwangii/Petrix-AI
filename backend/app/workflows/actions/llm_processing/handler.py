import json
from dataclasses import dataclass

from app.workflows.actions.llm_processing.client import ProcessingResult
from app.workflows.chat_script import ScriptError, evaluate
from app.workflows.templates import render


@dataclass(frozen=True)
class LLMOutcome:
    path_id: str
    outcome: str
    error: object
    model: str
    knowledge_sources: int


def prompt_value(source, mode, context, maximum, allow_empty=False):
    if mode == "expression":
        value = evaluate(source, context.environment())
        text = value if type(value) is str else json.dumps(value, ensure_ascii=False, allow_nan=False)
    elif not source and allow_empty: return ""
    else: text = render(source, context, maximum=maximum)
    if len(text) > maximum or (not text.strip() and not allow_empty): raise ScriptError("The processing input is empty or too long.")
    return text


def execute(config, context):
    model = context.llm_client.model if context.llm_client else ""
    sources = 0
    valid_input = True
    try:
        input_text = prompt_value(config["input"], config["input_mode"], context, 16000)
        system_prompt = prompt_value(config["system_prompt"], config["system_prompt_mode"], context, 8000, allow_empty=True)
    except (ScriptError, ValueError, TypeError):
        valid_input = False
        result = ProcessingResult("error", error="invalid_input")
    # Read inputs first, then reserve room and clear stale results before a request.
    try: context.assign(config["variable_name"], None)
    except ScriptError:
        return LLMOutcome(config["error_path_id"], "error", "variable_limit", model, 0)
    if valid_input:
        if not context.llm_client: result = ProcessingResult("error", error="provider_unavailable")
        else:
            result, sources = context.llm_client.process(input_text, system_prompt, config["temperature"],
                config["timeout_seconds"], config["use_knowledge_info"], context.language)
    if result.outcome == "success":
        if type(result.text) is not str or not result.text.strip():
            result = ProcessingResult("error", error="invalid_response")
        else:
            try: context.assign(config["variable_name"], result.text)
            except ScriptError: result = ProcessingResult("error", error="variable_limit")
    return LLMOutcome(config[result.outcome + "_path_id"], result.outcome, result.error, model, sources)
