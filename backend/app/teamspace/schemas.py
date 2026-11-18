import uuid
from typing import List,Literal,Optional,Dict,Any
from urllib.parse import urlsplit
from pydantic import Field,StrictInt,StrictBool,model_validator,field_validator
from app.agent_schemas import StrictModel,LANGUAGE_CODES

class Revision(StrictModel):expected_revision:StrictInt=Field(ge=1)
class AnalysisType(StrictModel):
 auto_since:Optional[str]=None
 enabled:bool=False
 auto:bool=False
 manual:bool=False
 threshold:StrictInt=Field(default=4,ge=0,le=10000)
 channels:List[Literal['webchat','messaging','bitrix24','africas_talking','tester','email']]=Field(default_factory=list,max_length=6)
 sentiment:bool=True
 emotion:bool=True
 summary:bool=True
 prompt:str=Field(default='',max_length=4000)
class AnalysisConfig(Revision):
 chats:AnalysisType=Field(default_factory=AnalysisType)
 tickets:AnalysisType=Field(default_factory=AnalysisType)
 people:AnalysisType=Field(default_factory=AnalysisType)
class CopilotConfig(Revision):
 all_conversations:bool=False
 person_history:bool=False
 current_conversation:bool=True
 @model_validator(mode='after')
 def dependencies(self):
  if not self.current_conversation:raise ValueError('Access to the current conversation is required.')
  if self.all_conversations:self.person_history=True
  return self
class AssignmentConfig(Revision):
 section:Literal['method','limit','priority']
 mode:Literal['balanced','random','round_robin','balanced_round_robin','manual','least_busy']='balanced'
 limit:StrictInt=Field(default=10,ge=1,le=100)
 priority_enabled:bool=False
 priority_type:Literal['skip_queue','ignore_limit']='skip_queue'
 tag_ids:List[uuid.UUID]=Field(default_factory=list,max_length=100)
 group_ids:List[uuid.UUID]=Field(default_factory=list,max_length=100)
 confirm_voluntary:bool=False
class EnforcementConfig(Revision):
 enabled:bool=False
 minimum:StrictInt=Field(default=1,ge=0,le=100)
 sort:Literal['a_z','z_a','newest']='a_z'
class MandatoryConfig(Revision):
 enabled:bool=False
 field_ids:List[uuid.UUID]=Field(default_factory=list,max_length=100)
class OverdueConfig(Revision):
 enabled:bool=False
 days:StrictInt=Field(default=15,ge=1,le=3650)
 exclude_weekends:bool=False
 user_id:Optional[uuid.UUID]=None
 team_ids:List[uuid.UUID]=Field(default_factory=list,max_length=100)
class EmbedConfig(Revision):
 enabled:bool=False
 url:str=Field(default='',max_length=2048)
 @model_validator(mode='after')
 def valid(self):
  parsed=urlsplit(self.url)
  if self.enabled and (parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password):raise ValueError('Enter an http:// or https:// URL without credentials.')
  return self
def bounded_duration(value):
 factors={'minutes':1,'hours':60,'days':1440,'weeks':10080,'months':43200}
 if value.duration is not None and value.unit in factors and value.duration*factors[value.unit]>5256000:raise ValueError('Ban duration cannot exceed ten years. Use Permanent for a permanent ban.')
 return value

class RuleBody(StrictModel):
 offences:StrictInt=Field(ge=0,le=10000)
 duration:StrictInt=Field(default=60,ge=1,le=525600)
 unit:Literal['minutes','hours','days','weeks','months','permanent']='minutes'
 expected_revision:Optional[StrictInt]=Field(default=None,ge=1)
 @model_validator(mode='after')
 def valid_duration(self):return bounded_duration(self)
class ReasonBody(StrictModel):
 name:str=Field(min_length=1,max_length=200)
 description:str=Field(default='',max_length=2000)
 expected_revision:Optional[StrictInt]=Field(default=None,ge=1)
class CannedBody(StrictModel):
 name:str=Field(min_length=1,max_length=100)
 language:str=Field(default='en',max_length=16)
 content:str=Field(min_length=1,max_length=4000)
 expected_revision:Optional[StrictInt]=Field(default=None,ge=1)
 @field_validator('language')
 @classmethod
 def language_code(cls,value):
  if value not in LANGUAGE_CODES:raise ValueError('Select a supported language.')
  return value
class Selection(StrictModel):ids:List[uuid.UUID]=Field(min_length=1,max_length=1000)
class LabelsBody(StrictModel):
 tag_ids:List[uuid.UUID]=Field(default_factory=list,max_length=100)
 group_ids:List[uuid.UUID]=Field(default_factory=list,max_length=100)
 mode:Literal['replace','add']='replace'
 expected_revision:Optional[StrictInt]=Field(default=None,ge=0)
class FieldsBody(StrictModel):
 values:Dict[str,Any]
 expected_revision:StrictInt=Field(ge=1)
class BanBody(StrictModel):
 reason_id:uuid.UUID
 duration:Optional[StrictInt]=Field(default=None,ge=1,le=525600)
 unit:Optional[Literal['minutes','hours','days','weeks','months','permanent']]=None
 expected_revision:StrictInt=Field(ge=1)
 @model_validator(mode='after')
 def valid_duration(self):return bounded_duration(self)
class RequestJob(StrictModel):request_id:uuid.UUID
class ExportBody(RequestJob):
 kind:Literal['chats','tickets','people']
 ids:List[uuid.UUID]=Field(min_length=1,max_length=5000)
 fields:List[str]=Field(min_length=1,max_length=150)
 transcript:bool=False
 timezone:str=Field(default='Africa/Nairobi',max_length=100)
 email:bool=False
 @field_validator('timezone')
 @classmethod
 def time_zone(cls,value):
  from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
  try:ZoneInfo(value)
  except (ZoneInfoNotFoundError,ValueError):raise ValueError('Select a valid IANA timezone.') from None
  return value
class BulkLabels(Selection,LabelsBody):kind:Literal['chats','tickets','people']
class PersonBody(StrictModel):
 name:str=Field(default='',max_length=200)
 email:str=Field(default='',max_length=320)
 phone:str=Field(default='',max_length=50)
 location:str=Field(default='',max_length=200)
 country:str=Field(default='',max_length=2)
 external_id:Optional[str]=Field(default=None,max_length=200)
 expected_revision:Optional[StrictInt]=Field(default=None,ge=1)

 @field_validator('email')
 @classmethod
 def valid_email(cls,value):
  if value:
   from pydantic import TypeAdapter,EmailStr
   value=str(TypeAdapter(EmailStr).validate_python(value))
  return value
 @field_validator('external_id')
 @classmethod
 def external(cls,value):return value.strip() or None if value else None
