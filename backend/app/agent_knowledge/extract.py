"""Text-only ingestion. No scripts, macros, OCR, or external resource loading."""
import io,json,re,zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath
from xml.etree import ElementTree
from app.agent_knowledge.schemas import MAX_FILE_BYTES,MAX_TEXT_BYTES,EXTENSIONS

class ImportFailure(ValueError):
 def __init__(self,code):self.code=code;super().__init__(code)

class VisibleHTML(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=True);self.parts=[];self.stack=[];self.skip=0
 def handle_starttag(self,tag,attrs):
  attrs=dict(attrs);hidden=tag in {'head','script','style','svg','noscript','template','nav','footer','form'} or 'hidden' in attrs or attrs.get('aria-hidden')=='true' or bool(re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',attrs.get('style',''),re.I))
  if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
   self.stack.append((tag,hidden));self.skip+=int(hidden)
  if not self.skip and tag in {'p','div','br','li','h1','h2','h3','h4','tr','section','article'}:self.parts.append('\n')
 def handle_startendtag(self,tag,attrs):
  if tag=='br' and not self.skip:self.parts.append('\n')
 def handle_endtag(self,tag):
  for i in range(len(self.stack)-1,-1,-1):
   if self.stack[i][0]==tag:
    self.skip-=sum(int(hidden) for _,hidden in self.stack[i:]);del self.stack[i:];break
  if not self.skip and tag in {'p','div','li','h1','h2','h3','h4','tr','section','article'}:self.parts.append('\n')
 def handle_data(self,data):
  if not self.skip:self.parts.append(data)

def normalize(text):
 text=text.replace('\x00','');text='\n'.join(re.sub(r'[^\S\n]+',' ',line).strip() for line in text.splitlines());text=re.sub(r'\n{3,}','\n\n',text).strip()
 if not text:raise ImportFailure('no_text')
 if len(text.encode('utf-8'))>MAX_TEXT_BYTES:raise ImportFailure('text_too_large')
 return text

def html_text(text):
 if re.search(r'<title[^>]*>\s*(just a moment|attention required)|cf_chl_opt|captcha-delivery.com',text,re.I):raise ImportFailure('website_challenge')
 parser=VisibleHTML();parser.feed(text);parser.close();return normalize(''.join(parser.parts))

def decode(data):
 try:return data.decode('utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
 except UnicodeError:raise ImportFailure('text_encoding') from None

def pdf_text(data):
 from pypdf import PdfReader,filters
 filters.ZLIB_MAX_OUTPUT_LENGTH=10*1024*1024
 if not data.startswith(b'%PDF-'):raise ImportFailure('invalid_pdf')
 reader=PdfReader(io.BytesIO(data),strict=True)
 if reader.is_encrypted:raise ImportFailure('encrypted_pdf')
 if len(reader.pages)>200:raise ImportFailure('too_many_pages')
 parts=[];size=0;streams=0
 for page in reader.pages:
  stream=page.get_contents()
  if stream:
   streams+=len(stream.get_data())
   if streams>16*1024*1024:raise ImportFailure('document_too_complex')
  text=page.extract_text() or '';size+=len(text.encode('utf-8'))
  if size>MAX_TEXT_BYTES:raise ImportFailure('text_too_large')
  parts.append(text)
 return normalize('\n\n'.join(parts))

def docx_text(data):
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  items=z.infolist()
  if len(items)>500 or sum(p.file_size for p in items)>20*1024*1024 or any(p.flag_bits&1 for p in items):raise ImportFailure('document_too_complex')
  if any('..' in PurePosixPath(p.filename).parts or p.filename.endswith('.bin') for p in items):raise ImportFailure('unsupported_document')
  names=z.namelist()
  if 'word/document.xml' not in names or '[Content_Types].xml' not in names:raise ImportFailure('invalid_docx')
  parts=[]
  for name in names:
   if name=='word/document.xml' or re.fullmatch(r'word/(header|footer)\d+\.xml',name):
    raw=z.read(name)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ImportFailure('invalid_docx')
    tree=ElementTree.fromstring(raw)
    for paragraph in tree.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
     parts.append(''.join(e.text or '' for e in paragraph.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t')))
  return normalize('\n'.join(parts))

def extract(data,filename):
 if not data or len(data)>MAX_FILE_BYTES:raise ImportFailure('file_too_large' if data else 'empty_file')
 extension=PurePosixPath(filename.lower()).suffix
 if extension not in EXTENSIONS:raise ImportFailure('unsupported_document')
 try:
  if extension=='.pdf':return pdf_text(data)
  if extension=='.docx':return docx_text(data)
  text=decode(data)
  if extension in {'.html','.htm'}:return html_text(text)
  if extension=='.json':
   json.loads(text)  # Reject corrupt or excessively nested JSON; retain source formatting.
  return normalize(text)
 except ImportFailure:raise
 except Exception:raise ImportFailure('document_invalid') from None

def chunks(text):
 result=[];start=0
 while start<len(text):
  end=min(start+1600,len(text))
  if end<len(text):
   split=text.rfind('\n',start+1000,end)
   if split>start:end=split
  result.append(text[start:end].strip())
  if end==len(text):break
  start=max(start+1,end-160)
 return result
