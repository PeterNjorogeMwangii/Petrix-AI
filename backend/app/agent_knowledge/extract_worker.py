"""Isolated document parser, terminated by its parent after 20 seconds."""
import base64,json,sys

def main():
 try:
  try:
   import resource
   resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024));resource.setrlimit(resource.RLIMIT_CPU,(15,15))
  except (ImportError,OSError,ValueError):pass
  from app.agent_knowledge.extract import extract,ImportFailure
  value=json.load(sys.stdin);text=extract(base64.b64decode(value['data'],validate=True),value['filename']);json.dump({'text':text},sys.stdout)
 except Exception as error:json.dump({'error':getattr(error,'code','document_invalid')},sys.stdout)

if __name__=='__main__':main()
