"""Bounded adapter for a server-owned public collection service; never fetches user URLs."""
from datetime import datetime,timezone
from typing import List,Literal,Optional
from urllib.parse import urlsplit
from pydantic import Field,field_validator
import httpx
from app.agent_schemas import StrictModel
from app.perception.schemas import LANGUAGES

class CollectionError(Exception):pass
class PublicEntry(StrictModel):
    external_id:str=Field(min_length=1,max_length=500)
    content:str=Field(min_length=1,max_length=30000)
    url:str=Field(min_length=1,max_length=2048)
    posted_at:datetime
    kind:Literal['post','comment','reply']='post'
    keyword_ids:List[str]=Field(default_factory=list,max_length=50)
    language:str=Field('',max_length=12)
    sentiment:Literal['positive','negative','neutral','mixed','unknown']='unknown'
    @field_validator('content')
    @classmethod
    def nonempty(cls,v):
        if not v.strip() or '\x00' in v:raise ValueError('Invalid public content.')
        return v.strip()
    @field_validator('posted_at')
    @classmethod
    def aware(cls,v):
        if v.tzinfo is None:raise ValueError('Publication dates must include a timezone.')
        return v.astimezone(timezone.utc)
    @field_validator('url')
    @classmethod
    def public_url(cls,v):
        try:
            u=urlsplit(v)
            if u.scheme!='https' or u.username or u.password or u.port not in {None,443} or not u.hostname or '\x00' in v:raise ValueError
        except ValueError:raise ValueError('Entries require valid HTTPS public URLs.') from None
        return v
    @field_validator('language')
    @classmethod
    def lang(cls,v):
        import re
        if v and not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?',v):raise ValueError('Use a language code.')
        return v
class Page(StrictModel):
    entries:List[PublicEntry]=Field(default_factory=list,max_length=500)
    next_cursor:Optional[str]=Field(None,max_length=4000)
    complete:bool=True
    public_source_verified:bool=False

class Collector:
    def __init__(self,settings):self.settings=settings
    @property
    def available(self):return bool(self.settings.perception_provider_url)
    def collect(self,payload):
        if not self.available:raise CollectionError('Public collection is not configured. Ask your administrator to connect the Perception collection service.')
        headers={'Content-Type':'application/json','Idempotency-Key':payload['run_id']+':'+str(payload.get('page',0))}
        if self.settings.perception_provider_token.get_secret_value():headers['Authorization']='Bearer '+self.settings.perception_provider_token.get_secret_value()
        try:
            with httpx.Client(timeout=httpx.Timeout(45,connect=10),follow_redirects=False,trust_env=False) as client:
                with client.stream('POST',self.settings.perception_provider_url,headers=headers,json=payload) as response:
                    if response.status_code<200 or response.status_code>=300:raise CollectionError('The collection service could not complete this run. Check the connection and platform access, then retry.')
                    chunks=[];size=0
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>2*1024*1024:raise CollectionError('The collection service response exceeded the page size limit.')
                        chunks.append(chunk)
            result=Page.model_validate_json(b''.join(chunks))
            if not result.public_source_verified:raise CollectionError('The collection service could not verify this public page, profile or search.')
            if not result.complete and not result.next_cursor:raise CollectionError('The collection service omitted the next page cursor.')
            if result.complete and result.next_cursor:raise CollectionError('The collection service returned an inconsistent completion state.')
            return result
        except httpx.TimeoutException:raise CollectionError('Collection timed out. You can retry this run.') from None
        except (httpx.HTTPError,ValueError,TypeError):raise CollectionError('The collection service returned an invalid response. Check its configuration, then retry.') from None
