"""Bounded Meta Graph transport. Provider errors never echo credentials."""
import base64,hashlib,ipaddress,json,logging,re,socket
from dataclasses import dataclass
from urllib.parse import urlsplit
import httpx


@dataclass
class GraphError(Exception):
    message: str
    uncertain: bool = False
    code: int = 0

    def __str__(self): return self.message


class RedactGraphURL(logging.Filter):
    def filter(self,record):
        if isinstance(record.args,tuple):
            safe=[]
            for value in record.args:
                if isinstance(value,httpx.URL) and value.host=='graph.facebook.com':
                    for key in ('input_token','access_token','appsecret_proof'):
                        if key in value.params:value=value.copy_set_param(key,'REDACTED')
                safe.append(value)
            record.args=tuple(safe)
        return True


if not any(isinstance(f,RedactGraphURL) for f in logging.getLogger('httpx').filters):
    logging.getLogger('httpx').addFilter(RedactGraphURL())


def recipient(value):
    if re.fullmatch(r'[1-9][0-9]{4,19}',value): return {'to':value}
    if re.fullmatch(r'[A-Z]{2}\.(?:ENT\.)?[A-Za-z0-9]{1,128}',value):return {'recipient':value}
    raise GraphError('The WhatsApp recipient identity is invalid.')


class GraphClient:
    def __init__(self,settings,transport=None):
        self.version=settings.whatsapp_graph_version
        self.transport=transport

    def http(self):
        return httpx.Client(timeout=httpx.Timeout(25,connect=10),follow_redirects=False,trust_env=False,transport=self.transport)

    def request(self,method,path,token,params=None,payload=None,files=None,data=None):
        if not re.fullmatch(r'(?:[0-9]{3,32}(?:/(?:messages|media))?|debug_token)',path):raise GraphError('Invalid Meta API path.')
        try:
            with self.http() as client,client.stream(method,'https://graph.facebook.com/'+self.version+'/'+path,
                    headers={'Authorization':'Bearer '+token},params=params,json=payload,files=files,data=data) as response:
                body=bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body)>1024*1024:raise GraphError('Meta returned an oversized response.',method=='POST')
                try:value=json.loads(body)
                except (ValueError,UnicodeError):raise GraphError('Meta returned an unreadable response.',method=='POST') from None
                if response.status_code>=400 or not isinstance(value,dict) or value.get('error'):
                    raw=value.get('error',{}) if isinstance(value,dict) else {}
                    code=raw.get('code',0) if isinstance(raw,dict) else 0
                    code=code if type(code) is int else 0
                    messages={190:'The Meta access token is invalid or expired.',10:'The Meta token is missing required permissions.',200:'The Meta token cannot access the assigned WhatsApp assets.',131047:'The WhatsApp customer service window has closed.'}
                    raise GraphError(messages.get(code,'Meta rejected the request (code '+str(code or response.status_code)+').'),method=='POST' and response.status_code>=500,code)
                return value
        except GraphError:raise
        except httpx.HTTPError:raise GraphError('The Meta request could not be confirmed.',method=='POST') from None

    def verify(self,app_id,app_secret,access_token,phone_number_id):
        debug=self.request('GET','debug_token',app_id+'|'+app_secret,params={'input_token':access_token}).get('data',{})
        if not isinstance(debug,dict):raise GraphError('Meta returned an invalid token verification response.')
        if not debug.get('is_valid') or str(debug.get('app_id'))!=app_id:raise GraphError('The access token does not belong to this Meta App.')
        scopes={v for v in debug.get('scopes',[]) if isinstance(v,str)} if isinstance(debug.get('scopes'),list) else set()
        if not {'whatsapp_business_messaging','whatsapp_business_management'}<=scopes:
            raise GraphError('Grant whatsapp_business_messaging and whatsapp_business_management, then generate a new system user token.')
        from app.core.security import utcnow
        for field in ('expires_at','data_access_expires_at'):
            expires=debug.get(field,0)
            if type(expires) not in {int,float}:raise GraphError('Meta returned an invalid token expiry.')
            if expires and expires<=utcnow().timestamp():raise GraphError('The Meta access token has expired.')
        phone=self.request('GET',phone_number_id,access_token,params={'fields':'id,display_phone_number,verified_name'})
        if str(phone.get('id'))!=phone_number_id:raise GraphError('Meta did not confirm this Phone number ID.')
        return {'display_phone':str(phone.get('display_phone_number',''))[:60],'verified_name':str(phone.get('verified_name',''))[:200]}

    def send(self,phone_number_id,token,destination,payload,identifier):
        body={'messaging_product':'whatsapp','recipient_type':'individual',**recipient(destination),**payload,'biz_opaque_callback_data':str(identifier)}
        response=self.request('POST',phone_number_id+'/messages',token,payload=body)
        messages=response.get('messages',[]);messages=messages if isinstance(messages,list) else []
        identifier=messages[0].get('id') if messages and isinstance(messages[0],dict) else None
        if not isinstance(identifier,str) or not 1<=len(identifier)<=512:raise GraphError('Meta accepted the request without a usable message ID.',True)
        return identifier

    def upload(self,phone_number_id,token,content_type,name,data):
        response=self.request('POST',phone_number_id+'/media',token,
            files={'file':(name,data,content_type)},data={'messaging_product':'whatsapp','type':content_type})
        identifier=response.get('id','')
        if not isinstance(identifier,str) or not re.fullmatch(r'[0-9]{3,32}',identifier):raise GraphError('Meta did not return a valid media ID.')
        return identifier

    def download(self,identifier,token,name='',maximum=10*1024*1024):
        if not re.fullmatch(r'[0-9]{3,32}',identifier):raise GraphError('Invalid WhatsApp media ID.')
        info=self.request('GET',identifier,token)
        try:
            url=info.get('url','');parsed=urlsplit(url);host=parsed.hostname or '';port=parsed.port
        except (ValueError,TypeError,AttributeError):raise GraphError('Meta returned an invalid media location.') from None
        if parsed.scheme!='https' or parsed.username or parsed.password or port not in {None,443} or not any(host==d or host.endswith('.'+d) for d in ['fbsbx.com','fbcdn.net']):
            raise GraphError('Meta returned an unapproved media location.')
        try:size=int(info.get('file_size') or 0)
        except (ValueError,TypeError,OverflowError):raise GraphError('Meta returned an invalid media size.') from None
        if size>maximum:raise GraphError('This WhatsApp file exceeds the 10 MiB incoming limit.')
        if self.transport is None:
            try:
                addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
                if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):raise ValueError()
            except (OSError,ValueError):raise GraphError('The Meta media location is unavailable.') from None
        try:
            with self.http() as client,client.stream('GET',url,headers={'Authorization':'Bearer '+token}) as response:
                if response.status_code!=200:raise GraphError('The WhatsApp media file could not be downloaded.')
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>maximum:raise GraphError('This WhatsApp file exceeds the 10 MiB incoming limit.')
        except httpx.HTTPError:raise GraphError('The WhatsApp media download could not finish.') from None
        if not data:raise GraphError('The WhatsApp media file is empty.')
        expected=info.get('sha256')
        actual=hashlib.sha256(data).digest()
        if expected and expected not in {actual.hex(),base64.b64encode(actual).decode()}:raise GraphError('The WhatsApp file checksum did not match.')
        content_type=str(info.get('mime_type','application/octet-stream')).split(';')[0].strip().lower()
        if not re.fullmatch(r'[a-z0-9.+-]+/[a-z0-9.+-]+',content_type):content_type='application/octet-stream'
        extensions={'image/jpeg':'jpg','image/png':'png','application/pdf':'pdf','audio/ogg':'ogg','audio/mpeg':'mp3','video/mp4':'mp4','image/webp':'webp'}
        name=name.replace('\\','/').rsplit('/',1)[-1]
        name=''.join(c for c in name if ord(c)>=32 and ord(c)!=127)[:200] or 'whatsapp-'+identifier+'.'+extensions.get(content_type,'bin')
        return {'file_name':name,'content_type':content_type,'size_bytes':len(data),'data':bytes(data)}
