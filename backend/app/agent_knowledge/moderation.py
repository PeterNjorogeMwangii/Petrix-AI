"""Server-owned input/output moderation. Raw rejected content is never logged."""
import re
from dataclasses import dataclass
import httpx

@dataclass(frozen=True)
class Verdict:
 state:str
 categories:tuple=()

class Moderator:
 def __init__(self,settings,transport=None):self.settings=settings;self.transport=transport
 @property
 def configured(self):return bool(getattr(self.settings,'moderation_base_url',''))
 @property
 def required(self):return getattr(self.settings,'moderation_required',False)
 def check(self,text):
  if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}',text):return Verdict('blocked',('secrets',))
  if not self.configured:return Verdict('unavailable' if self.required else 'unconfigured')
  if len(text.encode())>128000:return Verdict('unavailable')
  try:
   key=self.settings.moderation_api_key.get_secret_value();headers={'Authorization':'Bearer '+key} if key else {}
   payload={'input':text}
   if self.settings.moderation_model:payload['model']=self.settings.moderation_model
   with httpx.Client(timeout=10,follow_redirects=False,trust_env=False,transport=self.transport) as client:
    with client.stream('POST',self.settings.moderation_base_url.rstrip('/')+'/moderations',headers=headers,json=payload) as response:
     response.raise_for_status();raw=bytearray()
     for part in response.iter_bytes():
      raw.extend(part)
      if len(raw)>65536:return Verdict('unavailable')
     import json
     body=json.loads(raw);results=body['results']
     if len(results)!=1 or type(results[0]['flagged']) is not bool:return Verdict('unavailable')
     categories=results[0].get('categories',{})
     if not isinstance(categories,dict) or any(type(v) is not bool for v in categories.values()):return Verdict('unavailable')
     return Verdict('blocked' if results[0]['flagged'] else 'allowed',tuple(k for k,v in categories.items() if v))
  except Exception:return Verdict('unavailable')
