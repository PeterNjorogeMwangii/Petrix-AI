import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import WorkspaceBilling,BillingOperation,BillingWebhook,Workspace,UsageEvent,Agent,AgentConversation,AgentMessage
from app.workspace_settings.billing import run_topup,billing
from test_workspace_settings import ws,custom_actor
from test_agents import agent_workspace

class Stripe:
 def __init__(self):self.calls=[];self.checkouts={};self.intents={};self.fail=False;self.uncertain=False;self.subscribed=False
 def request(self,method,path,data=None,key=None):
  self.calls.append((method,path,data,key))
  if method=='POST' and path=='customers':return {'id':'cus_workspace'}
  if method=='GET' and path.startswith('customers/'):return {'id':'cus_workspace','invoice_settings':{'default_payment_method':'pm_saved'}}
  if path=='subscriptions':return {'data':[{'id':'sub_pro','status':'active','default_payment_method':'pm_saved','items':{'data':[{'current_period_start':int((utcnow()-timedelta(days=2)).timestamp()),'current_period_end':int((utcnow()+timedelta(days=28)).timestamp()),'price':{'id':'price_pro','currency':'usd','recurring':{'interval':'month','interval_count':1}}}]}}] if self.subscribed else []}
  if path=='invoices/create_preview':return {'amount_due':2900}
  if path=='checkout/sessions':
   identifier='cs_'+str(key).replace('-','');self.checkouts[identifier]={'id':identifier,'customer':data['customer'],'metadata':{'operation_id':data['metadata[operation_id]']},'line_items':{'data':[{'price':{'id':data['line_items[0][price]']},'quantity':int(data['line_items[0][quantity]'])}]},'mode':data['mode'],'status':'complete','payment_status':'paid','subscription':'sub_pro' if data['mode']=='subscription' else None};return {'id':identifier,'url':'https://checkout.stripe.com/c/test'}
  if path.startswith('checkout/sessions/'):return self.checkouts[path.rsplit('/',1)[1]]
  if path=='billing_portal/sessions':return {'url':'https://billing.stripe.com/p/session'}
  if path.startswith('prices/'):return {'id':path.rsplit('/',1)[1],'active':True,'unit_amount':500,'currency':'usd'}
  if path=='payment_intents':
   identifier='pi_'+str(key).replace('-','')
   if identifier not in self.intents:self.intents[identifier]={'id':identifier,'customer':data['customer'],'amount':int(data['amount']),'currency':data['currency'],'metadata':{'operation_id':data['metadata[operation_id]']},'status':'requires_payment_method' if self.fail else 'succeeded'}
   if self.uncertain:raise HTTPException(502,'Unconfirmed')
   return self.intents[identifier]
  if path.startswith('payment_intents/'):
   identifier=path.split('/')[1]
   if path.endswith('/confirm'):self.intents[identifier]['status']='succeeded'
   return self.intents[identifier]
  raise AssertionError((method,path,data))

@pytest.fixture
def stripe(app):
 app.state.settings.stripe_secret_key=SecretStr('sk_test_controlled')
 app.state.settings.stripe_webhook_secret=SecretStr('whsec_controlled')
 app.state.settings.stripe_products_json=json.dumps({'pro':{'name':'Pro','price':'price_pro','mode':'subscription','included':10,'limits':{'teams':5,'users':20}},'bundle':{'name':'Pro bundle','price':'price_bundle','mode':'payment','credits':100,'plans':['pro']}})
 fake=Stripe();app.state.stripe_client=fake;return fake

def purchase(client,ws,product='pro',quantity=1,identifier=None):
 root,path,_,headers=ws;identifier=identifier or str(uuid.uuid4())
 result=client.post(path+'/billing/checkout',json={'product':product,'quantity':quantity,'operation_id':identifier},headers=headers)
 return result,identifier

def webhook(client,event,secret='whsec_controlled',timestamp=None):
 raw=json.dumps(event).encode();ts=str(timestamp or int(time.time()));signature=hmac.new(secret.encode(),ts.encode()+b'.'+raw,hashlib.sha256).hexdigest()
 return client.post('/api/billing/stripe-webhook',content=raw,headers={'Stripe-Signature':'t='+ts+',v1='+signature,'Content-Type':'application/json'})


def test_billing_unconfigured_honest_usage_and_unlimited_agents(client,ws):
 root,path,_,headers=ws
 response=client.get(path+'/billing');assert response.status_code==200,response.text
 result=response.json();assert not result['configured'] and result['subscription']=={} and result['usage']['remaining'] is None
 assert next(v for v in result['limits'] if v['key']=='ai_agents')['limit'] is None
 assert client.post(path+'/billing/portal',json={},headers=headers).status_code==503

@pytest.mark.parametrize('quantity',[0,51,True,1.5,'two'])
def test_checkout_quantity_validation(client,ws,stripe,quantity):
 result,_=purchase(client,ws,'bundle',quantity);assert result.status_code==422
 assert not stripe.calls


def test_checkout_portal_idempotency_and_no_client_activation(client,app,ws,stripe):
 root,path,_,headers=ws
 result,identifier=purchase(client,ws);assert result.status_code==200,result.text
 assert purchase(client,ws,identifier=identifier)[0].status_code==200
 assert client.get(path+'/billing').json()['plan_name']=='Starter'
 operation_id=next(iter(stripe.checkouts));stripe.subscribed=True
 event={'id':'evt_purchase','type':'checkout.session.completed','data':{'object':stripe.checkouts[operation_id]}}
 assert webhook(client,event).status_code==200
 report=client.get(path+'/billing').json();assert report['plan_name']=='Pro' and report['subscription']['next_invoice_amount']==2900
 assert 'default_payment_method' not in report['subscription'] and report['subscription']['payment_method_saved']
 assert webhook(client,event).status_code==200
 with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(BillingWebhook))==1
 assert purchase(client,ws)[0].status_code==409
 assert client.post(path+'/billing/portal',json={},headers=headers).json()['url'].startswith('https://billing.stripe.com/')
 assert client.get(path+'/billing/usage-options').status_code==200

@pytest.mark.parametrize('kind',['bad_signature','stale','tampered_price','tampered_quantity'])
def test_webhook_authentication_and_saved_purchase_match(client,app,ws,stripe,kind):
 result,identifier=purchase(client,ws);assert result.status_code==200
 checkout=next(iter(stripe.checkouts.values()));event={'id':'evt_bad_'+kind,'type':'checkout.session.completed','data':{'object':checkout}}
 if kind=='tampered_price':checkout['line_items']['data'][0]['price']['id']='price_other'
 if kind=='tampered_quantity':checkout['line_items']['data'][0]['quantity']=50
 response=webhook(client,event,secret='wrong' if kind=='bad_signature' else 'whsec_controlled',timestamp=int(time.time())-1000 if kind=='stale' else None)
 assert response.status_code==400,response.text
 with app.state.session_factory() as db:assert db.get(BillingOperation,uuid.UUID(identifier)).status=='pending'


def configured_topup(client,app,ws,stripe):
 root,path,_,headers=ws;workspace_id=uuid.UUID(root.split('/')[3]);stripe.subscribed=True
 with app.state.session_factory() as db:
  row=billing(db,workspace_id);row.customer_id='cus_workspace';db.get(Workspace,workspace_id).plan_name='Pro';db.commit()
 assert client.post(path+'/billing/refresh',json={},headers=headers).status_code==200
 result=client.post(path+'/billing/auto-topup',json={'enabled':True,'product':'bundle','threshold':10,'expected_revision':1},headers=headers);assert result.status_code==200,result.text
 return workspace_id


def test_auto_topup_worker_and_same_failed_payment_retry(client,app,ws,stripe):
 identifier=configured_topup(client,app,ws,stripe);stripe.fail=True
 with app.state.session_factory() as db:assert run_topup(db,identifier,stripe,app.state.settings)=='failed'
 root,path,_,headers=ws;failed=client.get(path+'/billing').json()['topup'];assert failed['status']=='failed'
 before=len([v for v in stripe.calls if v[1]=='payment_intents'])
 with app.state.session_factory() as db:assert run_topup(db,identifier,stripe,app.state.settings)=='failed'
 assert len([v for v in stripe.calls if v[1]=='payment_intents'])==before
 retried=client.post(path+'/billing/topup/'+failed['id']+'/retry',json={},headers=headers)
 assert retried.status_code==200 and retried.json()['status']=='succeeded',retried.text
 assert len(stripe.intents)==1
 report=client.get(path+'/billing').json();assert report['usage']['purchased']==100 and report['usage']['remaining']==110
 with app.state.session_factory() as db:assert run_topup(db,identifier,stripe,app.state.settings)=='not_needed'


def test_uncertain_topup_reuses_same_idempotency_key(client,app,ws,stripe):
 identifier=configured_topup(client,app,ws,stripe);stripe.uncertain=True
 with app.state.session_factory() as db:
  with pytest.raises(HTTPException):run_topup(db,identifier,stripe,app.state.settings)
 path=ws[1];headers=ws[3];failed=client.get(path+'/billing').json()['topup'];assert failed['status']=='pending'
 stripe.uncertain=False
 assert client.post(path+'/billing/topup/'+failed['id']+'/retry',json={},headers=headers).json()['status']=='succeeded'
 assert len(stripe.intents)==1
 create_calls=[c for c in stripe.calls if c[1]=='payment_intents'];assert len(create_calls)==2 and create_calls[0][3]==create_calls[1][3]


def test_view_billing_cannot_charge_or_open_mutating_portal(client,app,ws,stripe):
 root,path,_,headers=ws;custom_actor(app,root.split('/')[3],['view_billing'])
 assert client.get(path+'/billing').status_code==200
 assert client.post(path+'/billing/portal',json={},headers=headers).status_code==403
 assert purchase(client,ws)[0].status_code==403
 assert not stripe.calls


def test_bundle_balance_carries_forward_after_monthly_reset(client,app,ws,stripe):
 identifier=configured_topup(client,app,ws,stripe)
 with app.state.session_factory() as db:
  assert run_topup(db,identifier,stripe,app.state.settings)=='succeeded'
  row=db.get(WorkspaceBilling,identifier);snapshot=dict(row.snapshot);current=utcnow();last=current.replace(day=1)-timedelta(days=1);last_start=last.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
  snapshot['usage_ledger']={'start':last_start.isoformat(),'end':current.replace(day=1,hour=0,minute=0,second=0,microsecond=0).isoformat(),'included':10,'prior_consumed':0,'anchor_day':1};row.snapshot=snapshot
  db.add(UsageEvent(workspace_id=identifier,event_key='last-month',category='sql_query',source='workflow',units=15,created_at=last_start+timedelta(days=1)));db.commit()
 result=client.get(ws[1]+'/billing');assert result.status_code==200,result.text
 assert result.json()['usage']['bundle_remaining']==95
 # Reading again settles each previous period only once.
 assert client.get(ws[1]+'/billing').json()['usage']['bundle_remaining']==95


def test_incomplete_subscription_cannot_activate_paid_permissions(client,app,ws,stripe):
 stripe.subscribed=True
 original=stripe.request
 def incomplete(method,path,data=None,key=None):
  result=original(method,path,data,key)
  if path=='subscriptions':result['data'][0]['status']='incomplete'
  return result
 stripe.request=incomplete
 with app.state.session_factory() as db:
  row=billing(db,uuid.UUID(ws[0].split('/')[3]));row.customer_id='cus_workspace';db.commit()
 result=client.post(ws[1]+'/billing/refresh',json={},headers=ws[3]);assert result.status_code==200,result.text
 assert result.json()['plan_name']=='Starter' and result.json()['subscription']['included'] is None
