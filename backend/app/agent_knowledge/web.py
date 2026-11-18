"""A public HTTPS single-page reader; every DNS answer and redirect is pinned."""
import time,zlib,socket
from dataclasses import dataclass
from urllib.parse import urlsplit,urljoin
from urllib.robotparser import RobotFileParser
import httpx
from app.agent_knowledge.schemas import url_value,MAX_FILE_BYTES
from app.agent_knowledge.extract import ImportFailure
from app.workflows.actions.send_api.client import SendAPIClient,BlockedEndpoint

USER_AGENT='PetrixAI-Knowledge/0.26'
@dataclass(frozen=True)
class WebDocument:
 data:bytes
 filename:str
 final_url:str

class WebsiteReader:
 def __init__(self,transport_factory=None,resolver=None):self.pinning=SendAPIClient(transport_factory=transport_factory,resolver=resolver)
 def request(self,url,deadline,maximum):
  try:
   url_value(url);pinned,host,authority=self.pinning.pinned_url(url)
   remaining=deadline-time.monotonic()
   if remaining<=0:raise ImportFailure('fetch_timeout')
   transport=self.pinning.transport_factory() if self.pinning.transport_factory else httpx.HTTPTransport(verify=self.pinning.ssl_context,retries=0,trust_env=False)
   with httpx.Client(transport=transport,timeout=min(remaining,15),follow_redirects=False,trust_env=False) as client:
    with client.stream('GET',pinned,headers={'Host':authority,'User-Agent':USER_AGENT,'Accept':'text/html,text/plain,application/pdf','Accept-Encoding':'identity'},extensions={'sni_hostname':host}) as response:
     encoding=response.headers.get('content-encoding','identity').lower();decoder=zlib.decompressobj(16+zlib.MAX_WBITS) if encoding=='gzip' else None
     if encoding not in {'','identity','gzip'}:raise ImportFailure('unsupported_encoding')
     data=bytearray()
     for part in response.iter_raw():
      if time.monotonic()>deadline:raise ImportFailure('fetch_timeout')
      if decoder:
       part=decoder.decompress(part,maximum-len(data)+1)
       if decoder.unconsumed_tail:raise ImportFailure('response_too_large')
      data.extend(part)
      if len(data)>maximum:raise ImportFailure('response_too_large')
     if decoder and (not decoder.eof or decoder.unused_data):raise ImportFailure('website_invalid')
     return response.status_code,dict(response.headers),bytes(data)
  except ImportFailure:raise
  except BlockedEndpoint:raise ImportFailure('blocked_endpoint') from None
  except (httpx.TimeoutException,socket.timeout):raise ImportFailure('fetch_timeout') from None
  except Exception:raise ImportFailure('website_unavailable') from None
 def robots(self,url,deadline):
  parts=urlsplit(url);robot_url=parts.scheme+'://'+parts.netloc+'/robots.txt'
  for _ in range(4):
   status,headers,raw=self.request(robot_url,deadline,128*1024)
   if status not in {301,302,303,307,308}:break
   robot_url=urljoin(robot_url,headers.get('location',''))
  else:raise ImportFailure('robots_unavailable')
  if status in {404,410}:return
  if status in {401,403}:raise ImportFailure('robots_disallowed')
  if not 200<=status<300:raise ImportFailure('robots_unavailable')
  parser=RobotFileParser();parser.parse(raw.decode('utf-8',errors='replace').splitlines())
  if not parser.can_fetch(USER_AGENT,url):raise ImportFailure('robots_disallowed')
 def fetch(self,url):
  deadline=time.monotonic()+25;seen=set()
  for _ in range(4):
   try:url_value(url)
   except ValueError:raise ImportFailure('blocked_endpoint') from None
   if url in seen:raise ImportFailure('redirect_limit')
   seen.add(url);self.robots(url,deadline)
   status,headers,raw=self.request(url,deadline,MAX_FILE_BYTES)
   if status in {301,302,303,307,308}:
    location=headers.get('location','')
    if not location:raise ImportFailure('website_invalid')
    url=urljoin(url,location);continue
   if status in {401,403,429}:raise ImportFailure('website_blocked')
   if not 200<=status<300:raise ImportFailure('website_unavailable')
   mime=headers.get('content-type','').split(';')[0].strip().lower()
   filename='page.pdf' if mime=='application/pdf' else 'page.html' if mime in {'text/html','application/xhtml+xml'} else 'page.txt' if mime=='text/plain' else None
   if not filename:raise ImportFailure('unsupported_website')
   return WebDocument(raw,filename,url)
  raise ImportFailure('redirect_limit')
