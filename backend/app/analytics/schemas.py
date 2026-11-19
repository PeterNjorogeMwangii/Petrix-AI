import uuid
from typing import Any,List,Literal,Optional
from pydantic import Field,field_validator
from app.agent_schemas import StrictModel

class CustomFilter(StrictModel):
    key:str=Field(min_length=1,max_length=64)
    value:Any

class Filters(StrictModel):
    chat_type:Literal['all','ai','live']='all'
    ai_agents:List[uuid.UUID]=Field(default_factory=list,max_length=50)
    live_agents:List[uuid.UUID]=Field(default_factory=list,max_length=50)
    teams:List[uuid.UUID]=Field(default_factory=list,max_length=50)
    channels:List[str]=Field(default_factory=list,max_length=20)
    statuses:List[Literal['open','pending','resolved','overdue','closed','ai','external','in_progress']]=Field(default_factory=list,max_length=8)
    ratings:List[int]=Field(default_factory=list,max_length=5)
    unrated:bool=False
    tags:List[uuid.UUID]=Field(default_factory=list,max_length=50)
    groups:List[uuid.UUID]=Field(default_factory=list,max_length=50)
    custom_fields:List[CustomFilter]=Field(default_factory=list,max_length=10)
    creator_type:Literal['all','ai','live']='all'
    include_tests:bool=False
    @field_validator('ratings')
    @classmethod
    def ratings_valid(cls,values):
        if any(type(v) is not int or not 1<=v<=5 for v in values):raise ValueError('Ratings must be integers from 1 to 5.')
        return sorted(set(values))
    @field_validator('channels')
    @classmethod
    def channels_valid(cls,values):
        allowed={'webchat','whatsapp','facebook','messaging','email','tester','ai_agent_handoff','bitrix24','africas_talking'}
        if set(values)-allowed:raise ValueError('Choose a supported recorded channel.')
        return sorted(set(values))

class ActivityBody(StrictModel):
    pass
