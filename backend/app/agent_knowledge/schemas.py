import uuid
from typing import Optional,Literal,List,Dict
from urllib.parse import urlsplit
from pydantic import Field,StrictBool,field_validator
from app.agent_schemas import StrictModel
from app.workflows.actions.send_api.config import validate_endpoint

MAX_FILE_BYTES=10*1024*1024
MAX_TEXT_BYTES=256*1024
MAX_AGENT_TEXT=2*1024*1024
MAX_AGENT_FILES=50*1024*1024
MAX_SOURCES=50
EXTENSIONS={'.txt','.md','.csv','.json','.html','.htm','.pdf','.docx'}

def url_value(value):
 value=value.strip();validate_endpoint(value)
 if urlsplit(value).port not in {None,443}:raise ValueError('Knowledge URLs use public HTTPS on port 443.')
 return value

class SnippetBody(StrictModel):
 id:uuid.UUID=Field(default_factory=uuid.uuid4)
 name:str=Field(min_length=1,max_length=100)
 content:str=Field(min_length=1,max_length=20000)
 enabled:StrictBool=True
 @field_validator('name','content')
 @classmethod
 def clean(cls,v):
  if not v.strip() or '\x00' in v:raise ValueError('Enter non-empty text without null characters.')
  return v.strip()

class URLBody(StrictModel):
 id:uuid.UUID=Field(default_factory=uuid.uuid4)
 name:str=Field(min_length=1,max_length=100)
 url:str=Field(max_length=2048)
 enabled:StrictBool=True
 @field_validator('url')
 @classmethod
 def valid_url(cls,v):return url_value(v)

class SourceEdit(StrictModel):
 expected_revision:int=Field(ge=1)
 name:str=Field(min_length=1,max_length=100)
 enabled:StrictBool
 content:Optional[str]=Field(default=None,min_length=1,max_length=20000)
 url:Optional[str]=Field(default=None,max_length=2048)
 @field_validator('url')
 @classmethod
 def valid_url(cls,v):return url_value(v) if v is not None else v

 @field_validator('content')
 @classmethod
 def clean_content(cls,v):
  if v is not None and (not v.strip() or '\x00' in v):raise ValueError('Enter non-empty text without null characters.')
  return v

class SourceRevision(StrictModel):
 expected_revision:int=Field(ge=1)

class DeleteSources(StrictModel):
 ids:List[uuid.UUID]=Field(min_length=1,max_length=50)
 revisions:Dict[uuid.UUID,int]

class PolicyBody(StrictModel):
 expected_revision:int=Field(ge=1)
 master_prompt:str=Field(default='',max_length=12000)
 use_for_replies:StrictBool=True
 fallback_message:str=Field(default="Sorry, I can't respond to that request.",min_length=1,max_length=500)
 @field_validator('master_prompt')
 @classmethod
 def valid_prompt(cls,v):
  import re
  if '\x00' in v:raise ValueError('Prompts cannot contain null characters.')
  allowed={'_chatbot.name','agent.name','language','current_date'}
  if any(m not in allowed for m in re.findall(r'\{([A-Za-z_][A-Za-z0-9_.]*)\}',v)):raise ValueError('Master prompts support {_chatbot.name}, {agent.name}, {language} and {current_date}. Use workflow variables in LLM Processing.')
  return v
