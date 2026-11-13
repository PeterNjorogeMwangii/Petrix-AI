"""Explicit allow-list: never import an action path supplied by a client."""
from importlib import import_module

from app.workflows.actions.ask_survey.config import AskSurveyConfig
from app.workflows.actions.ask_survey.handler import execute as ask_survey
from app.workflows.actions.auto_fill_field.config import AutoFillFieldConfig
from app.workflows.actions.auto_fill_field.handler import execute as auto_fill_field
from app.workflows.actions.auto_team_match.config import AutoTeamMatchConfig
from app.workflows.actions.auto_team_match.handler import execute as auto_team_match
from app.workflows.actions.branch.config import BranchConfig
from app.workflows.actions.branch.handler import execute as branch
from app.workflows.actions.collect_feedback.config import CollectFeedbackConfig
from app.workflows.actions.collect_feedback.handler import execute as collect_feedback
from app.workflows.actions.close_chat.config import CloseChatConfig
from app.workflows.actions.close_chat.handler import execute as close_chat
from app.workflows.actions.jump.config import JumpConfig
from app.workflows.actions.jump.handler import execute as jump
from app.workflows.actions.live_agent_handoff.config import LiveAgentHandoffConfig
from app.workflows.actions.live_agent_handoff.handler import execute as live_agent_handoff
from app.workflows.actions.llm_processing.config import LLMProcessingConfig
from app.workflows.actions.llm_processing.handler import execute as llm_processing
from app.workflows.actions.send_attachment.config import SendAttachmentConfig
from app.workflows.actions.send_attachment.handler import execute as send_attachment
from app.workflows.actions.show_carousel.config import ShowCarouselConfig
from app.workflows.actions.show_carousel.handler import execute as show_carousel
from app.workflows.actions.send_graph.config import SendGraphConfig
from app.workflows.actions.send_graph.handler import execute as send_graph
from app.workflows.actions.send_message.config import SendMessageConfig
from app.workflows.actions.send_message.handler import execute as send_message
from app.workflows.actions.send_api.config import SendAPIConfig
from app.workflows.actions.send_api.handler import execute as send_api
from app.workflows.actions.set_variable.config import SetVariableConfig
from app.workflows.actions.set_variable.handler import execute as set_variable
from app.workflows.actions.sql_query.config import SQLQueryConfig
from app.workflows.actions.sql_query.handler import execute as sql_query
from app.workflows.actions.switch_language.config import SwitchLanguageConfig
from app.workflows.actions.switch_language.handler import execute as switch_language

ACTION_TYPES = (
    "send_message", "send_attachment", "show_carousel", "ask_survey",
    "live_agent_handoff", "external_handoff", "ai_agent_handoff", "create_ticket",
    "collect_feedback", "jump", "branch", "set_variable", "switch_language", "close_chat",
    "send_api", "llm_processing", "auto_fill_field", "auto_team_match", "sql_query", "send_graph",
)
ACTIONS = {key: import_module("app.workflows.actions." + key).DEFINITION for key in ACTION_TYPES}


def config_for(action_type):
    return {"send_message": SendMessageConfig, "send_attachment": SendAttachmentConfig, "show_carousel": ShowCarouselConfig, "set_variable": SetVariableConfig, "jump": JumpConfig,
        "ask_survey": AskSurveyConfig, "branch": BranchConfig, "send_api": SendAPIConfig, "sql_query": SQLQueryConfig,
        "switch_language": SwitchLanguageConfig, "collect_feedback": CollectFeedbackConfig, "close_chat": CloseChatConfig,
        "llm_processing": LLMProcessingConfig, "auto_fill_field": AutoFillFieldConfig, "auto_team_match": AutoTeamMatchConfig, "send_graph": SendGraphConfig, "live_agent_handoff": LiveAgentHandoffConfig}[action_type]


def handler_for(action_type):
    return {"send_message": send_message, "send_attachment": send_attachment, "show_carousel": show_carousel, "set_variable": set_variable, "jump": jump,
        "ask_survey": ask_survey, "branch": branch, "send_api": send_api, "sql_query": sql_query,
        "switch_language": switch_language, "collect_feedback": collect_feedback, "close_chat": close_chat,
        "llm_processing": llm_processing, "auto_fill_field": auto_fill_field, "auto_team_match": auto_team_match, "send_graph": send_graph, "live_agent_handoff": live_agent_handoff}[action_type]
