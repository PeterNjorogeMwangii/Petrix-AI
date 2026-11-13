from typing import Literal
from app.agent_schemas import StrictModel


class ExternalHandoffConfig(StrictModel):
    platform: Literal['zendesk', 'bitrix24']
