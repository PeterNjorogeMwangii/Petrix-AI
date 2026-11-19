import re,uuid
from datetime import date,datetime,timezone
from typing import List,Literal,Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pydantic import Field,field_validator,model_validator
from app.agent_schemas import StrictModel

PLATFORMS={'facebook':'Facebook','instagram':'Instagram','x':'X (Twitter)','tiktok':'TikTok','youtube':'YouTube','app_store':'App Store','play_store':'Play Store','news':'News'}
LANGUAGES={'en':'English','sw':'Swahili','fr':'French','ar':'Arabic','es':'Spanish','pt':'Portuguese','de':'German','it':'Italian','hi':'Hindi','zh':'Chinese','ja':'Japanese','ko':'Korean','ru':'Russian','tr':'Turkish','am':'Amharic','so':'Somali','rw':'Kinyarwanda','lg':'Luganda','zu':'Zulu','af':'Afrikaans'}

def key(value):return ' '.join(value.casefold().split())
def page_url(platform,value):
    try:
        u=urlsplit(value.strip());host=(u.hostname or '').lower();path=u.path.strip('/')
        if u.scheme!='https' or u.username or u.password or u.port not in {None,443} or u.fragment:raise ValueError
        if platform=='x':
            if host not in {'x.com','www.x.com','mobile.x.com','twitter.com','www.twitter.com','mobile.twitter.com'} or u.query or not re.fullmatch(r'[A-Za-z0-9_]{1,15}',path) or path.lower() in {'home','explore','search','notifications','messages','settings','i','intent','share','compose','login','signup','hashtag','tos','privacy','help','about'}:raise ValueError
            return 'https://x.com/'+path
        if host not in {'facebook.com','www.facebook.com','m.facebook.com','mbasic.facebook.com'}:raise ValueError
        if u.query or not (re.fullmatch(r'[A-Za-z0-9._-]+',path) or re.fullmatch(r'pages/[A-Za-z0-9._-]+/[0-9]+',path)) or path.lower().split('/')[0] in {'groups','events','watch','marketplace','reel','reels','stories','share','login','settings','search','profile.php','permalink.php','photo.php','help','posts','dialog'}:raise ValueError
        return 'https://www.facebook.com/'+path
    except (ValueError,TypeError):raise ValueError('Invalid X (Twitter) profile URL.' if platform=='x' else 'Enter a valid Facebook page URL.') from None

class Revision(StrictModel):
    expected_revision:int=Field(ge=1)
class Confirmation(Revision):
    confirmed:bool=False
class ConfigBody(Confirmation):
    use_custom:bool=True
    use_teams:bool=False
    translate:bool=False
    target_language:str='en'
    excluded_languages:List[str]=Field(default_factory=list,max_length=20)
    @field_validator('target_language')
    @classmethod
    def language(cls,v):
        if v not in LANGUAGES:raise ValueError('Choose a supported target language.')
        return v
    @field_validator('excluded_languages')
    @classmethod
    def excluded(cls,v):
        if set(v)-LANGUAGES.keys():raise ValueError('Choose supported excluded languages.')
        return sorted(set(v))
class KeywordBody(StrictModel):
    expected_revision:Optional[int]=Field(None,ge=1)
    name:str=Field(min_length=1,max_length=200)
    variations:List[str]=Field(default_factory=list,max_length=30)
    tag_ids:List[uuid.UUID]=Field(default_factory=list,max_length=50)
    enabled:bool=True
    confirmed:bool=False
    @field_validator('name')
    @classmethod
    def name_clean(cls,v):
        if not key(v):raise ValueError('Enter a main keyword.')
        return v.strip()
    @field_validator('variations')
    @classmethod
    def terms(cls,v):
        if any(not t.strip() or len(t)>200 for t in v):raise ValueError('Use keyword variations of 1–200 characters.')
        return list(dict.fromkeys(t.strip() for t in v))
class TeamBody(StrictModel):
    enabled:bool
    expected_revision:int=Field(ge=0)
    tag_ids:List[uuid.UUID]=Field(default_factory=list,max_length=50)
    confirmed:bool=False
class Selection(StrictModel):
    ids:List[uuid.UUID]=Field(default_factory=list,max_length=100)
    all_matching:bool=False
    q:str=Field('',max_length=256)
    status:Literal['all','enabled','disabled']='all'
    operation:Literal['enable','disable','delete']='disable'
    confirmed:bool=False
class Range(StrictModel):
    start:str=Field(min_length=10,max_length=40)
    end:str=Field(min_length=10,max_length=40)
class SourceBody(StrictModel):
    expected_revision:Optional[int]=Field(None,ge=1)
    platform:str
    method:Literal['page','keyword']='page'
    name:str=Field(min_length=1,max_length=200)
    url:str=Field('',max_length=2048)
    keyword_ids:List[uuid.UUID]=Field(default_factory=list,max_length=50)
    recurring:bool=False
    interval_unit:Literal['days','hours','minutes']='hours'
    interval_value:int=24
    timezone:str=Field('Africa/Nairobi',max_length=64)
    historical:Optional[Range]=None
    @model_validator(mode='after')
    def valid(self):
        if self.platform not in {'facebook','x'}:raise ValueError('Scraping is not supported for this platform yet.')
        self.name=self.name.strip()
        if not self.name:raise ValueError('Enter a source name.')
        if self.method=='page':
            self.url=page_url(self.platform,self.url)
            if self.keyword_ids:raise ValueError('Page sources cannot link keyword groups.')
        else:
            self.url=''
            if not self.keyword_ids and self.expected_revision is None:raise ValueError('Select at least one active keyword group.')
        lower,upper={'days':(1,30),'hours':(1,720),'minutes':(10,720)}[self.interval_unit]
        if not lower<=self.interval_value<=upper:raise ValueError('Recurring intervals must be 1–30 days, 1–720 hours, or 10–720 minutes.')
        try:ZoneInfo(self.timezone)
        except (ValueError,ZoneInfoNotFoundError):raise ValueError('Choose a valid source timezone.') from None
        self.keyword_ids=list(dict.fromkeys(self.keyword_ids))
        return self
class ToggleSource(Revision):
    recurring:bool
class Filters(StrictModel):
    sources:List[uuid.UUID]=Field(default_factory=list,max_length=50)
    platforms:List[Literal['facebook','instagram','x','tiktok','youtube','app_store','play_store','news']]=Field(default_factory=list,max_length=8)
    sentiments:List[Literal['positive','negative','neutral','mixed','unknown']]=Field(default_factory=list,max_length=5)
    keywords:List[uuid.UUID]=Field(default_factory=list,max_length=50)
