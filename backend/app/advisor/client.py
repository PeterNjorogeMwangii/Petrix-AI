"""Server-owned OpenAI-compatible Advisor connection, with optional image input."""
import json
from dataclasses import dataclass
import httpx

@dataclass
class Reply:
    ok:bool
    text:str=''
    notice:str=''

class AdvisorAI:
    def __init__(self,settings):self.settings=settings
    @property
    def connection(self):
        s=self.settings
        if s.advisor_base_url and s.advisor_model:return s.advisor_base_url,s.advisor_api_key.get_secret_value(),s.advisor_model
        if s.support_ai_base_url and s.support_ai_model:return s.support_ai_base_url,s.support_ai_api_key.get_secret_value(),s.support_ai_model
        if s.llm_base_url and s.llm_model:return s.llm_base_url,s.llm_api_key.get_secret_value(),s.llm_model
        return '', '', ''
    @property
    def available(self):return bool(self.connection[0])
    @property
    def vision(self):return self.available and self.settings.advisor_vision_enabled
    def generate(self,messages):
        base,key,model=self.connection
        if not base:return Reply(False,notice='Advisor is using bundled documentation. Connect a model for conversational answers and attachment analysis.')
        headers={'Content-Type':'application/json'}
        if key:headers['Authorization']='Bearer '+key
        try:
            with httpx.Client(timeout=httpx.Timeout(35,connect=10),follow_redirects=False,trust_env=False) as client:
                with client.stream('POST',base.rstrip('/')+'/chat/completions',headers=headers,json={'model':model,'messages':messages,'temperature':0.2,'max_tokens':2400,'stream':False}) as response:
                    if response.status_code<200 or response.status_code>=300:return Reply(False,notice='The Advisor model could not respond. Guidance below comes from bundled documentation.')
                    parts=[];size=0
                    for piece in response.iter_bytes():
                        size+=len(piece)
                        if size>256*1024:raise ValueError
                        parts.append(piece)
            value=json.loads(b''.join(parts));content=value['choices'][0]['message']['content']
            if not isinstance(content,str) or not content.strip() or len(content)>18000:raise ValueError
            return Reply(True,text=content.strip())
        except (httpx.HTTPError,ValueError,TypeError,KeyError,IndexError):return Reply(False,notice='The Advisor model is unavailable or returned an invalid response. Guidance below comes from bundled documentation.')
