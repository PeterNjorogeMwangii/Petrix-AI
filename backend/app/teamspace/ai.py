import json
from dataclasses import dataclass
import httpx

@dataclass
class AIResult:
 ok:bool
 text:str=''
 error:str=''

class SupportAI:
 def __init__(self,settings):self.settings=settings
 @property
 def available(self):return bool(self.settings.support_ai_base_url and self.settings.support_ai_model)
 def generate(self,system,content):
  if not self.available:return AIResult(False,error='AI support is not configured. Set SUPPORT_AI_BASE_URL and SUPPORT_AI_MODEL on the server.')
  try:
   headers={'Content-Type':'application/json'}
   if self.settings.support_ai_api_key.get_secret_value():headers['Authorization']='Bearer '+self.settings.support_ai_api_key.get_secret_value()
   with httpx.Client(timeout=20,follow_redirects=False,trust_env=False) as client:
    response=client.post(self.settings.support_ai_base_url.rstrip('/')+'/chat/completions',headers=headers,json={'model':self.settings.support_ai_model,'messages':[{'role':'system','content':system},{'role':'user','content':content}],'temperature':0.2,'max_tokens':1800,'stream':False})
   if response.status_code>=400:return AIResult(False,error='The configured AI endpoint could not complete this request.')
   if len(response.content)>65536:raise ValueError
   data=response.json();text=data['choices'][0]['message']['content']
   if not isinstance(text,str) or not text.strip() or len(text)>16000:raise ValueError
   return AIResult(True,text=text.strip())
  except httpx.TimeoutException:return AIResult(False,error='The AI request timed out. You can retry it.')
  except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError):return AIResult(False,error='The AI endpoint returned an invalid response.')


def clean_analysis(text,config):
 value=text.strip()
 if value.startswith('```'):
  value=value[value.find('\n')+1:];value=value.rsplit('```',1)[0]
 try:
  data=json.loads(value)
  if not isinstance(data,dict):raise ValueError
  result={}
  if config.get('summary'):
   summary=data.get('summary')
   if not isinstance(summary,str) or not summary.strip() or len(summary)>8000:raise ValueError
   result['summary']=summary
  if config.get('sentiment'):
   if data.get('sentiment') not in {'positive','negative','neutral','mixed'}:raise ValueError
   result['sentiment']=data['sentiment']
  if config.get('emotion'):
   emotions=data.get('emotions')
   if not isinstance(emotions,list) or len(emotions)>10 or any(not isinstance(e,str) or len(e)>100 for e in emotions):raise ValueError
   result['emotions']=emotions
  return result
 except (ValueError,TypeError):raise ValueError('The AI returned an invalid analysis. No result or interaction charge was saved.') from None
