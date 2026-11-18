import logging,threading
from app.agent_knowledge.jobs import run_one

def start(app):
 stop=threading.Event()
 def loop():
  while not stop.is_set():
   try:run_one(app.state.session_factory,app.state.agent_knowledge_reader)
   except Exception as error:logging.getLogger(__name__).error('Knowledge worker failed (%s). Queued jobs remain saved.',type(error).__name__)
   stop.wait(5)
 thread=threading.Thread(target=loop,name='petrix-knowledge-worker',daemon=True);thread.start();return stop,thread
