import re,uuid
from typing import List,Optional,Literal,Dict
from urllib.parse import urlsplit
from pydantic import Field,StrictBool,StrictInt,field_validator,model_validator
from app.agent_schemas import StrictModel

class Welcome(StrictModel):
    enabled:StrictBool=True
    title:str=Field(default='Hello',max_length=100)
    description:str=Field(default='What can we help you with today?',max_length=500)
    button:str=Field(default='Start New Chat',min_length=1,max_length=80)
class Appearance(StrictModel):
    header:str='#000000'
    message:str='#000000'
    button:str='#000000'
    background:str='#FFFFFF'
    radius:StrictInt=Field(default=18,ge=0,le=32)
    title:str=Field(default='',max_length=100)
    subtitle:str=Field(default='Replies immediately',max_length=200)
    agent_icon:Optional[uuid.UUID]=None
    launcher_icon:Optional[uuid.UUID]=None
    position:Literal['left','right']='right'
    width:StrictInt=Field(default=380,ge=280,le=640)
    height:StrictInt=Field(default=620,ge=360,le=900)
    @field_validator('header','message','button','background')
    @classmethod
    def color(cls,v):
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',v):raise ValueError('Use a six-digit hexadecimal colour.')
        return v.upper()
class PreField(StrictModel):
    key:str=Field(min_length=1,max_length=64)
    label:str=Field(min_length=1,max_length=100)
    placeholder:str=Field(default='',max_length=150)
    required:StrictBool=False
class Prechat(StrictModel):
    enabled:StrictBool=False
    message:str=Field(default='Please fill in the form below before starting the chat.',max_length=500)
    button:str=Field(default='Send',min_length=1,max_length=80)
    fields:List[PreField]=Field(default_factory=list,max_length=20)
    @model_validator(mode='after')
    def unique(self):
        if len({v.key for v in self.fields})!=len(self.fields):raise ValueError('Choose each pre-chat field once.')
        if self.enabled and not self.fields:raise ValueError('Add at least one pre-chat field before enabling it.')
        return self
class Mobile(StrictModel):
    threshold:StrictInt=Field(default=768,ge=320,le=1440)
    mode:Literal['fullscreen','hide','size']='fullscreen'
    width:StrictInt=Field(default=95,ge=40,le=100)
    height:StrictInt=Field(default=90,ge=40,le=100)
class Proactive(StrictModel):
    id:uuid.UUID=Field(default_factory=uuid.uuid4)
    name:str=Field(default='Welcome message',min_length=1,max_length=100)
    enabled:StrictBool=True
    seconds:StrictInt=Field(default=10,ge=0,le=86400)
    api_only:StrictBool=False
    rules:List[str]=Field(default_factory=lambda:['*'],max_length=30)
    messages:List[str]=Field(default_factory=lambda:['Hello! How can we help?'],min_length=1,max_length=5)
    buttons:List[str]=Field(default_factory=list,max_length=5)
    @field_validator('messages','buttons')
    @classmethod
    def short(cls,v):
        if any(not s.strip() or len(s)>500 for s in v):raise ValueError('Use non-empty messages/buttons up to 500 characters.')
        return v
    @field_validator('rules')
    @classmethod
    def patterns(cls,v):
        if any(not s or len(s)>300 or any(ord(c)<32 for c in s) or not(s.lstrip('!').startswith(('/','https://','http://')) or s=='*') for s in v):raise ValueError('Use page paths or HTTP(S) URLs, * and ! exclusions.')
        return v
class WidgetLink(StrictModel):
    label:str=Field(min_length=1,max_length=60)
    url:str=Field(min_length=1,max_length=2048)
    @field_validator('url')
    @classmethod
    def safe(cls,v):
        p=urlsplit(v)
        if p.scheme not in {'https','mailto','tel'} or p.username or p.password or any(ord(c)<33 for c in v):raise ValueError('Use an HTTPS, mailto or tel destination.')
        return v
class Multi(StrictModel):
    enabled:StrictBool=False
    links:List[WidgetLink]=Field(default_factory=list,max_length=6)
class Configuration(StrictModel):
    language:str='en'
    icon:Optional[uuid.UUID]=None
    attachments:StrictBool=True
    emojis:StrictBool=True
    hide_offline:StrictBool=False
    ranking:StrictBool=True
    auto_close:StrictBool=False
    timeout_minutes:StrictInt=Field(default=15,ge=1,le=10080)
    audio:StrictBool=False
    voice:StrictBool=False
    playback:StrictBool=False
    welcome:Welcome=Field(default_factory=Welcome)
    appearance:Appearance=Field(default_factory=Appearance)
    prechat:Prechat=Field(default_factory=Prechat)
    mobile:Mobile=Field(default_factory=Mobile)
    multichannel:Multi=Field(default_factory=Multi)
    proactive:List[Proactive]=Field(default_factory=list,max_length=20)
    proactive_color:str='#FFFFFF'
    quick_color:str='#000000'
    quick_border:str='#000000'
    whitelist:StrictBool=False
    domains:List[str]=Field(default_factory=list,max_length=50)
    @field_validator('proactive_color','quick_color','quick_border')
    @classmethod
    def color(cls,v):return Appearance.color(v)
    @field_validator('domains')
    @classmethod
    def origins(cls,values):
        out=[]
        for v in values:
            p=urlsplit(v)
            if p.scheme not in {'https','http'} or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in {'','/'} or '*' in v or any(ord(c)<33 for c in v):raise ValueError('Use exact website origins, e.g. https://example.com. Add subdomains separately.')
            try:
                port=p.port
                if port is not None and port<1:raise ValueError()
            except ValueError:raise ValueError('Use a valid port.')
            host=p.hostname.encode('idna').decode().lower();host='['+host+']' if ':' in host else host
            out.append(p.scheme+'://'+host+(':'+str(port) if port and port!={'http':80,'https':443}[p.scheme] else ''))
        return list(dict.fromkeys(out))
    @model_validator(mode='after')
    def complete(self):
        if self.whitelist and not self.domains:raise ValueError('Add an approved website before enabling whitelisting.')
        if sum(len(v) for v in self.domains)>3000:raise ValueError('Use a shorter origin list for the browser framing policy.')
        if len({v.id for v in self.proactive})!=len(self.proactive):raise ValueError('Proactive message IDs must be unique.')
        return self
class Create(StrictModel):
    client_id:uuid.UUID
    name:str=Field(default='Default Webchat',min_length=1,max_length=100)
class Save(StrictModel):
    expected_revision:StrictInt=Field(ge=1)
    name:str=Field(min_length=1,max_length=100)
    enabled:StrictBool
    config:Configuration
class Preview(StrictModel):
    config:Configuration
class Delete(StrictModel):
    expected_revision:StrictInt=Field(ge=1)
    confirmed:StrictBool=False
class StartSession(StrictModel):
    token:str=Field(min_length=43,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')
    host_origin:str=Field(max_length=300)
class StartChat(StrictModel):
    request_id:uuid.UUID
    values:Dict[str,object]=Field(default_factory=dict,max_length=20)
class Event(StrictModel):
    request_id:uuid.UUID
    event:Literal['message_received','collect_feedback','chat_closed']='message_received'
    content:str=Field(default='',max_length=4000)
    survey_answer_token:Optional[uuid.UUID]=None
    feedback_token:Optional[uuid.UUID]=None
    rating:Optional[StrictInt]=Field(default=None,ge=1,le=5)
    feedback_answers:Dict[uuid.UUID,str]=Field(default_factory=dict,max_length=50)
    voice:StrictBool=False
    @model_validator(mode='after')
    def complete(self):
        if self.event=='message_received' and not self.content:raise ValueError('Enter a message.')
        if self.event=='collect_feedback' and self.feedback_token is None:raise ValueError('Use this conversation feedback request.')
        return self
class Rating(StrictModel):
    value:Literal['up','down']
