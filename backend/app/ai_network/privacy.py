"""Conservative structured-data filtering at every transfer boundary.

This removes known sensitive fields and formats, rather than making an unsupported
claim to detect every secret embedded in arbitrary customer prose.
"""
import json
import re
from app.workflows.chat_script import ScriptError

SENSITIVE=re.compile(r'(?:password|passwd|secret|token|credential|authorization|cookie|apikey|api_key|private|internal|session|connectionstring|databaseurl|cardnumber|card_number|cvv|pin_code|account_number|national_id)',re.I)
PRIVATE_KEYS={'url','headers','attachment','attachments','base64','pin','cvv','system','variables','agent_id','conversation_id','workspace_id'}
VALUE_SECRET=re.compile(r'(?:Bearer\s+[A-Za-z0-9._~+/=-]+|(?:postgres(?:ql)?|mysql|redis)://\S+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|(?:password|api[_ -]?key|secret|token|pin|cvv)\s*[:=]\s*\S+)',re.I)


def text(value):
    return VALUE_SECRET.sub('[redacted]',value)


def clean(value,depth=0):
    if depth>8:return None
    if type(value) is dict:
        if {'url','content_type','name'}.issubset(value):return {'name':value.get('name'),'content_type':value.get('content_type')}
        return {key:clean(child,depth+1) for key,child in value.items() if type(key) is str and not key.startswith('_') and key.lower() not in PRIVATE_KEYS and not SENSITIVE.search(key.replace('-','').replace(' ',''))}
    if type(value) is list:return [clean(child,depth+1) for child in value[:100]]
    if type(value) is str:
        if value.startswith('/api/') or '?X-Amz-' in value or '?sig=' in value:return None
        return text(value)
    if value is None or type(value) in {bool,int,float}:return value
    return None


def context(variables):
    previous=variables.get('_transferred_context',{})
    merged={**(previous if type(previous) is dict else {}),**{key:value for key,value in variables.items() if key!='_transferred_context'}}
    result=clean(merged)
    try:encoded=json.dumps(result,ensure_ascii=False,allow_nan=False).encode()
    except (ValueError,OverflowError,RecursionError):raise ScriptError('The transferred context must contain finite JSON data.') from None
    if len(result)>100 or len(encoded)>32768:raise ScriptError('The filtered transferred context exceeds 100 variables or 32 KiB. Reduce the public context before handing off.')
    return result


def redact_history(value,variables):
    hidden=[]
    def collect(item,sensitive=False):
        if type(item) is dict:
            for key,child in item.items():collect(child,sensitive or key.lower() in PRIVATE_KEYS or bool(SENSITIVE.search(key)))
        elif type(item) is list:
            for child in item:collect(child,sensitive)
        elif sensitive and type(item) in {str,int} and 4<=len(str(item))<=2048:hidden.append(str(item))
    collect(variables)
    value=text(value)
    for secret in sorted(set(hidden),key=len,reverse=True):value=value.replace(secret,'[redacted]')
    return value


def public_variables(variables):
    result=clean({key:value for key,value in variables.items() if key!='_transferred_context'})
    if '_transferred_context' in variables:result['_transferred_context']=clean(variables['_transferred_context'])
    return result
