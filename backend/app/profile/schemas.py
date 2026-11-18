import uuid
from typing import Literal,Optional
from pydantic import Field,StrictInt,StrictBool,field_validator,model_validator
from app.agent_schemas import StrictModel,LANGUAGE_CODES

class AccountBody(StrictModel):
 first_name:str=Field(default='',max_length=100)
 last_name:str=Field(default='',max_length=100)
 language:str='en'
 expected_revision:StrictInt=Field(ge=1)
 @field_validator('language')
 @classmethod
 def valid_language(cls,value):
  if value not in LANGUAGE_CODES:raise ValueError('Select a supported language.')
  return value
 @field_validator('first_name','last_name')
 @classmethod
 def safe_name(cls,value):
  if any(ord(c)<32 for c in value):raise ValueError('Names cannot contain control characters.')
  return value
class Notifications(StrictModel):
 browser:StrictBool=False
 platform:StrictBool=True
 assigned_you:Literal['none','byte','chime','bell']='byte'
 assigned_team:Literal['none','byte','chime','bell']='none'
 transferred_you:Literal['none','byte','chime','bell']='byte'
 message_open:Literal['none','byte','chime','bell']='none'
 message_other:Literal['none','byte','chime','bell']='byte'
class NotificationBody(StrictModel):
 expected_revision:StrictInt=Field(ge=1)
 notifications:Notifications
class PreferenceBody(StrictModel):
 expected_revision:StrictInt=Field(ge=1)
 presence:Optional[Literal['online','away','offline']]=None
 theme:Optional[Literal['system','light','dark']]=None
 multichat:Optional[StrictBool]=None
class AliasBody(StrictModel):
 name:str=Field(min_length=1,max_length=100)
 is_default:StrictBool=False
 signature_name:str=Field(default='',max_length=200)
 signature_content:str=Field(default='',max_length=4000)
 expected_revision:Optional[StrictInt]=Field(default=None,ge=1)
 @field_validator('name','signature_name','signature_content')
 @classmethod
 def no_controls(cls,value,info):
  allowed='\n\r\t' if info.field_name=='signature_content' else ''
  if any(ord(c)<32 and c not in allowed for c in value):raise ValueError('Text contains an invalid control character.')
  return value

class Revision(StrictModel):expected_revision:StrictInt=Field(ge=1)
class PersonaBody(StrictModel):
 alias_id:Optional[uuid.UUID]=None
 expected_revision:StrictInt=Field(ge=0)
class TicketReply(StrictModel):
 expected_revision:StrictInt=Field(ge=1)
 client_request_id:uuid.UUID
 content:str=Field(min_length=1,max_length=8000)
 alias_id:Optional[uuid.UUID]=None
 domain_id:uuid.UUID
