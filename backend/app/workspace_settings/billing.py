import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlsplit
from typing import Optional, Literal
import httpx
from fastapi import APIRouter, Depends, Request, HTTPException, Query, Response
from pydantic import Field, StrictInt
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from app.database import get_db
from app.agent_schemas import StrictModel
from app.core.security import utcnow
from app.models import WorkspaceBilling, BillingOperation, BillingWebhook, Workspace, User, Agent, Teamspace, Membership, SupportTeam, WorkspaceInvite
from app.workspace_settings.routes import actor, revision
from app.workspace_settings import permissions as p, usage
router=APIRouter(prefix='/api',tags=['Workspace billing'])
API_VERSION='2025-06-30.basil'


class StripeClient:
 def __init__(self,settings):self.settings=settings
 def request(self,method,path,data=None,key=None):
  secret=self.settings.stripe_secret_key.get_secret_value()
  if not secret:raise HTTPException(503,'Stripe billing is not configured. Ask your administrator to configure Stripe products and keys.')
  headers={'Stripe-Version':API_VERSION}
  if key:headers['Idempotency-Key']='petrix-'+str(key)
  try:
   with httpx.Client(timeout=httpx.Timeout(20,connect=5),follow_redirects=False) as client:
    response=client.request(method,'https://api.stripe.com/v1/'+path,auth=(secret,''),headers=headers,data=data if method=='POST' else None,params=data if method=='GET' else None)
   if response.status_code>=400:
    if response.status_code==402:
     intent=response.json().get('error',{}).get('payment_intent')
     if isinstance(intent,dict) and intent.get('id'):return intent
     raise HTTPException(409,'The payment needs attention. Update your saved payment method in Manage billing, then retry the same top-up.')
    raise HTTPException(502,'Stripe could not complete this request. Check your billing configuration or retry shortly.')
   return response.json()
  except (httpx.HTTPError,ValueError):raise HTTPException(502,'Stripe did not confirm the request. Retry to check the same operation.') from None


def products(settings):
 try:catalog=json.loads(settings.stripe_products_json)
 except ValueError:catalog={}
 return catalog if isinstance(catalog,dict) else {}


def billing(db,workspace_id,lock=False):
 db.execute(insert(WorkspaceBilling).values(workspace_id=workspace_id,snapshot={},auto_topup={},revision=1).on_conflict_do_nothing())
 query=select(WorkspaceBilling).where(WorkspaceBilling.workspace_id==workspace_id)
 return db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


def client(request):return request.app.state.stripe_client


def customer(db,workspace,user_id,stripe):
 row=billing(db,workspace.id,True)
 if not row.customer_id:
  user=db.get(User,user_id)
  result=stripe.request('POST','customers',{'name':workspace.name,'email':user.email,'metadata[workspace_id]':str(workspace.id)},'customer-'+str(workspace.id))
  row.customer_id=result['id'];db.commit()
 return row


def refresh(db,row,stripe,settings,commit=True):
 if not row.customer_id:return
 counters(db,row,settings)
 customer_data=stripe.request('GET','customers/'+quote(row.customer_id,safe=''))
 subs=stripe.request('GET','subscriptions',{'customer':row.customer_id,'status':'all','limit':100})
 active=[s for s in subs.get('data',[]) if s.get('status') not in {'canceled','incomplete_expired'}]
 current=next((s for s in active if s['id']==row.subscription_id),active[0] if active else None)
 snapshot={'usage_ledger':(row.snapshot or {}).get('usage_ledger'), 'payment_method_saved':bool((customer_data.get('invoice_settings') or {}).get('default_payment_method'))}
 if current:
  items=current.get('items',{}).get('data',[]);item=items[0] if items else {};price=item.get('price',{})
  product=next((v for v in products(settings).values() if v.get('mode')=='subscription' and v.get('price')==price.get('id')),None)
  start=item.get('current_period_start');end=item.get('current_period_end')
  snapshot.update({'status':current['status'],'period_start':start,'period_end':end,'periodicity':price.get('recurring',{}),'currency':price.get('currency'),'included':product.get('included') if product else None,'limits':product.get('limits',{}) if product else {},'payment_method_saved':bool(current.get('default_payment_method')) or snapshot['payment_method_saved'],'default_payment_method':current.get('default_payment_method') or (customer_data.get('invoice_settings') or {}).get('default_payment_method'),'next_invoice_amount':None})
  if isinstance(snapshot['default_payment_method'],dict):snapshot['default_payment_method']=snapshot['default_payment_method'].get('id')
  try:snapshot['next_invoice_amount']=stripe.request('POST','invoices/create_preview',{'customer':row.customer_id,'subscription':current['id']})['amount_due']
  except HTTPException:pass
  row.subscription_id=current['id']
  if product and current['status'] in {'active','trialing','past_due','unpaid'}:
   workspace=db.get(Workspace,row.workspace_id);workspace.plan_name=product.get('name','Custom')
  else:
   db.get(Workspace,row.workspace_id).plan_name='Starter' if current['status']=='incomplete' else 'Custom'
   snapshot['included']=None;snapshot['limits']={}
 else:row.subscription_id=None;snapshot['status']='no_subscription';db.get(Workspace,row.workspace_id).plan_name='Starter'
 row.snapshot=snapshot
 if commit:db.commit()
 else:db.flush()


def window(row):
 now=utcnow();start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0);end=(start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1))
 saved=row.snapshot or {}
 # Interactions reset monthly even on multi-month subscription periods.
 if saved.get('period_start') and saved.get('period_end'):
  anchor=datetime.fromtimestamp(saved['period_start'],timezone.utc)
  import calendar
  for offset in range(0,61):
   month=anchor.month-1+offset;year=anchor.year+month//12;month=month%12+1
   candidate=anchor.replace(year=year,month=month,day=min(anchor.day,calendar.monthrange(year,month)[1]))
   if candidate>now:end=candidate;break
   start=candidate
 return start,end


def counters(db,row,settings):
 start,end=window(row);report=usage.aggregate(db,row.workspace_id,settings,start,end,group='month')
 included=row.snapshot.get('included');used=report['total'];base=float(included) if included is not None else None
 saved=dict(row.snapshot or {});ledger=saved.get('usage_ledger') or {'start':start.isoformat(),'end':end.isoformat(),'included':base,'prior_consumed':0,'anchor_day':start.day}
 prior=float(ledger.get('prior_consumed',0))
 previous_start=datetime.fromisoformat(ledger['start']);previous_end=datetime.fromisoformat(ledger['end'])
 previous_allowance=ledger.get('included')
 # Settle missed monthly periods before applying the current period. Purchased
 # bundles carry forward, so an idle workspace never loses paid interactions.
 import calendar
 for _ in range(120):
  if previous_start>=start:break
  boundary=min(previous_end,start)
  past=usage.aggregate(db,row.workspace_id,settings,previous_start,boundary,group='month')['total']
  if previous_allowance is not None:prior+=max(0,past-float(previous_allowance))
  previous_start=boundary
  month=previous_end.month%12+1;year=previous_end.year+(1 if previous_end.month==12 else 0)
  previous_end=previous_end.replace(year=year,month=month,day=min(ledger.get('anchor_day',start.day),calendar.monthrange(year,month)[1]))
 purchased=float(db.scalar(select(func.coalesce(func.sum(BillingOperation.credits),0)).where(BillingOperation.workspace_id==row.workspace_id,BillingOperation.status=='succeeded')) or 0)
 additional=max(0,used-base) if base is not None else 0
 balance=max(0,purchased-prior-additional)
 saved['usage_ledger']={'start':start.isoformat(),'end':end.isoformat(),'included':base,'prior_consumed':prior,'anchor_day':ledger.get('anchor_day',start.day)}
 row.snapshot=saved
 return {'used':used,'included':base,'remaining':max(0,base-used)+balance if base is not None else None,'additional_used':additional,'purchased':purchased,'bundle_remaining':balance,'reset_at':end.isoformat(),'period_start':start.isoformat(),'percentage':round(used/base*100,2) if base else None}


def plan_data(db,workspace,settings,row):
 counts={name:db.scalar(select(func.count()).select_from(model).where(model.workspace_id==workspace.id,*([SupportTeam.archived.is_(False)] if model==SupportTeam else []))) or 0 for name,model in [('teamspaces',Teamspace),('teams',SupportTeam),('users',Membership),('ai_agents',Agent)]}
 counts['users']+=db.scalar(select(func.count()).select_from(WorkspaceInvite).where(WorkspaceInvite.workspace_id==workspace.id,WorkspaceInvite.accepted_at.is_(None),WorkspaceInvite.revoked_at.is_(None),WorkspaceInvite.expires_at>utcnow())) or 0
 limit=row.snapshot.get('limits',{})
 options=[{'key':k,'name':v.get('name',k),'mode':v.get('mode'),'credits':v.get('credits'),'plans':v.get('plans',[])} for k,v in products(settings).items()]
 failed=db.scalars(select(BillingOperation).where(BillingOperation.workspace_id==workspace.id,BillingOperation.kind=='topup',BillingOperation.status.in_(['failed','pending','requires_action'])).order_by(BillingOperation.created_at.desc()).limit(1)).first()
 return {'plan_name':workspace.plan_name,'configured':bool(settings.stripe_secret_key.get_secret_value()),'subscription':{k:v for k,v in row.snapshot.items() if k not in {'default_payment_method','usage_ledger'}},'usage':counters(db,row,settings),'limits':[{'key':k,'used':v,'limit':None if k=='ai_agents' else limit.get(k)} for k,v in counts.items()],'can_manage':p.allowed(db,workspace.id,'manage_billing'),'products':options,'auto_topup':row.auto_topup,'revision':row.revision,'topup':{'id':str(failed.id),'status':failed.status} if failed else None,'credits_note':'Purchased bundle balances carry forward. The monthly plan allowance resets each month.'}


@router.get('/workspaces/{workspace_id}/workspace-settings/billing')
def get_billing(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 _,workspace,_=actor(request,db,workspace_id,'view_billing');row=billing(db,workspace_id,True);result=plan_data(db,workspace,request.app.state.settings,row);db.commit();return result


@router.post('/workspaces/{workspace_id}/workspace-settings/billing/refresh')
def refresh_billing(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 _,workspace,_=actor(request,db,workspace_id,'view_billing',True);row=billing(db,workspace_id,True);refresh(db,row,client(request),request.app.state.settings);return plan_data(db,workspace,request.app.state.settings,row)


class CheckoutBody(StrictModel):
 product:str=Field(min_length=1,max_length=100)
 quantity:StrictInt=Field(default=1,ge=1,le=50)
 operation_id:uuid.UUID


@router.post('/workspaces/{workspace_id}/workspace-settings/billing/checkout')
def checkout(workspace_id:uuid.UUID,body:CheckoutBody,request:Request,db:Session=Depends(get_db)):
 session,workspace,_=actor(request,db,workspace_id,'manage_billing',True);settings=request.app.state.settings;product=products(settings).get(body.product)
 if not product:raise HTTPException(422,'Select a configured billing product.')
 mode=product['mode']
 if mode=='subscription' and body.quantity!=1:raise HTTPException(422,'Choose one subscription.')
 if mode=='payment' and workspace.plan_name.lower() not in product.get('plans',['lite','pro','total']):raise HTTPException(403,'Additional bundles are available for the configured Lite, Pro, and Total plans.')
 row=customer(db,workspace,session.user_id,client(request))
 if mode=='subscription' and row.subscription_id:raise HTTPException(409,'Use Manage billing to change your existing subscription without creating a second subscription.')
 operation=db.get(BillingOperation,body.operation_id)
 if operation:
  if operation.workspace_id!=workspace_id or operation.detail.get('product')!=body.product or operation.quantity!=body.quantity:raise HTTPException(409,'Use the original operation details when retrying checkout.')
 else:
  operation=BillingOperation(id=body.operation_id,workspace_id=workspace_id,kind='subscription' if mode=='subscription' else 'bundle',quantity=body.quantity,detail={'product':body.product,'price':product['price'],'mode':mode,'credits':product.get('credits',0)});db.add(operation);db.commit()
 origin=settings.frontend_origin+'/app/settings/workspace/plan-billing'
 data={'customer':row.customer_id,'mode':mode,'line_items[0][price]':product['price'],'line_items[0][quantity]':str(body.quantity),'success_url':origin+'?billing=success','cancel_url':origin+'?billing=cancel','client_reference_id':str(workspace_id),'metadata[operation_id]':str(operation.id)}
 result=client(request).request('POST','checkout/sessions',data,operation.id);operation.provider_id=result['id'];db.commit()
 return {'url':safe_url(result['url'])}


def safe_url(value):
 parsed=urlsplit(value)
 if parsed.scheme!='https' or parsed.hostname not in {'checkout.stripe.com','billing.stripe.com','invoice.stripe.com','pay.stripe.com'}:raise HTTPException(502,'Stripe returned an unexpected redirect address.')
 return value


@router.post('/workspaces/{workspace_id}/workspace-settings/billing/portal')
def portal(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 session,workspace,_=actor(request,db,workspace_id,'manage_billing',True);row=customer(db,workspace,session.user_id,client(request))
 result=client(request).request('POST','billing_portal/sessions',{'customer':row.customer_id,'return_url':request.app.state.settings.frontend_origin+'/app/settings/workspace/plan-billing'})
 return {'url':safe_url(result['url'])}


class TopupBody(StrictModel):
 enabled:bool
 product:str=Field(default='',max_length=100)
 threshold:StrictInt=Field(ge=0,le=100000000)
 expected_revision:StrictInt=Field(ge=1)


@router.post('/workspaces/{workspace_id}/workspace-settings/billing/auto-topup')
def auto_topup(workspace_id:uuid.UUID,body:TopupBody,request:Request,db:Session=Depends(get_db)):
 _,workspace,_=actor(request,db,workspace_id,'manage_billing',True);row=billing(db,workspace_id,True);revision(row,body.expected_revision)
 if body.enabled:
  refresh(db,row,client(request),request.app.state.settings,commit=False);product=products(request.app.state.settings).get(body.product,{})
  if product.get('mode')!='payment' or workspace.plan_name.lower() not in product.get('plans',['lite','pro','total']):raise HTTPException(422,'Choose a bundle available for your plan.')
  if not row.snapshot.get('default_payment_method'):raise HTTPException(422,'Save a default payment method in Manage billing before enabling auto top-up.')
  if row.snapshot.get('included') is None:raise HTTPException(422,'Configure a monthly interaction allowance for your subscription before enabling auto top-up.')
 row.auto_topup={'enabled':body.enabled,'product':body.product,'threshold':body.threshold};row.revision+=1;db.commit();return {'saved':True}


def run_topup(db,workspace_id,stripe,settings,retry=None):
 row=billing(db,workspace_id,True);config=row.auto_topup or {}
 outstanding=db.scalar(select(BillingOperation).where(BillingOperation.workspace_id==workspace_id,BillingOperation.kind=='topup',BillingOperation.status.in_(['pending','failed','requires_action'])).order_by(BillingOperation.created_at).with_for_update())
 if outstanding and (not retry or outstanding.id!=retry):return outstanding.status
 if retry and not outstanding:raise HTTPException(404,'No failed top-up was found.')
 workspace=db.get(Workspace,workspace_id);product=products(settings).get(config.get('product'),{})
 if not retry:
  if not config.get('enabled'):return 'disabled'
  refresh(db,row,stripe,settings,commit=False)
  balance=counters(db,row,settings)['remaining']
  if balance is None or balance>config.get('threshold',0):return 'not_needed'
 if retry:refresh(db,row,stripe,settings,commit=False)
 if not row.customer_id or not row.snapshot.get('default_payment_method'):raise HTTPException(409,'Set a default payment method in Manage billing.')
 if product.get('mode')!='payment' or workspace.plan_name.lower() not in product.get('plans',['lite','pro','total']):raise HTTPException(409,'The configured top-up bundle is unavailable for this plan.')
 operation=outstanding
 if not operation:
  price=stripe.request('GET','prices/'+quote(product['price'],safe=''))
  if not price.get('active') or not isinstance(price.get('unit_amount'),int):raise HTTPException(409,'Configure an active fixed-price bundle.')
  operation=BillingOperation(workspace_id=workspace_id,kind='topup',detail={'product':config['product'],'price':product['price'],'amount':price['unit_amount'],'currency':price['currency'],'credits':product['credits'],'payment_method':row.snapshot['default_payment_method']});db.add(operation);db.commit()
 # Commit a durable intent before making the network call. Uncertain attempts
 # reuse the same Stripe idempotency key; never create a second charge blindly.
 try:
  if operation.provider_id:
   result=stripe.request('GET','payment_intents/'+quote(operation.provider_id,safe=''))
   if result['status']=='requires_payment_method' and retry:
    attempt=operation.detail.get('confirm_attempt',0)+(1 if operation.status in {'failed','requires_action'} else 0)
    operation.detail={**operation.detail,'confirm_attempt':attempt};operation.status='pending';db.commit()
    result=stripe.request('POST','payment_intents/'+quote(operation.provider_id,safe='')+'/confirm',{'payment_method':row.snapshot['default_payment_method'],'off_session':'true'},str(operation.id)+'-retry-'+str(attempt))
  else:
   result=stripe.request('POST','payment_intents',{'amount':str(operation.detail['amount']),'currency':operation.detail['currency'],'customer':row.customer_id,'payment_method':operation.detail['payment_method'],'confirm':'true','off_session':'true','metadata[operation_id]':str(operation.id)},operation.id)
  operation.provider_id=result['id'];apply_intent(operation,row,result);db.commit();return operation.status
 except HTTPException as error:
  operation.status='failed' if error.status_code==409 else 'pending';db.commit();raise


def apply_intent(operation,row,result):
 if result.get('customer')!=row.customer_id or result.get('metadata',{}).get('operation_id')!=str(operation.id) or result.get('amount')!=operation.detail['amount'] or result.get('currency')!=operation.detail['currency']:raise HTTPException(400,'Payment does not match its saved operation.')
 status=result.get('status');operation.status='succeeded' if status=='succeeded' else 'failed' if status in {'requires_payment_method','canceled'} else 'requires_action' if status=='requires_action' else 'pending'
 operation.credits=float(operation.detail['credits']) if status=='succeeded' else 0


@router.post('/workspaces/{workspace_id}/workspace-settings/billing/topup/{identifier}/retry')
def retry_topup(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_billing',True);return {'status':run_topup(db,workspace_id,client(request),request.app.state.settings,identifier)}


@router.post('/billing/stripe-webhook')
async def webhook(request:Request,db:Session=Depends(get_db)):
 settings=request.app.state.settings;secret=settings.stripe_webhook_secret.get_secret_value()
 if not secret:raise HTTPException(503,'Stripe webhooks are not configured.')
 raw=await request.body()
 if len(raw)>1024*1024:raise HTTPException(413,'Webhook is too large.')
 try:
  parts=[part.split('=',1) for part in request.headers.get('stripe-signature','').split(',')];timestamp=next(v for k,v in parts if k=='t')
  expected=hmac.new(secret.encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
  if abs(time.time()-int(timestamp))>300 or not any(k=='v1' and hmac.compare_digest(v,expected) for k,v in parts):raise ValueError
  event=json.loads(raw)
  if not re.fullmatch(r'evt_[A-Za-z0-9_]+',event['id']):raise ValueError
 except (ValueError,KeyError,StopIteration,TypeError):raise HTTPException(400,'Invalid Stripe webhook signature or body.') from None
 claimed=db.execute(insert(BillingWebhook).values(id=event['id']).on_conflict_do_nothing().returning(BillingWebhook.id)).scalar_one_or_none()
 if not claimed:return {'received':True}
 kind=event.get('type','');obj=event.get('data',{}).get('object',{});customer_id=obj.get('customer')
 row=db.scalar(select(WorkspaceBilling).where(WorkspaceBilling.customer_id==customer_id).with_for_update()) if customer_id else None
 if row:
  if kind in {'checkout.session.completed','checkout.session.async_payment_succeeded'}:
   current=client(request).request('GET','checkout/sessions/'+quote(obj['id'],safe=''),{'expand[]':'line_items'})
   identifier=current.get('metadata',{}).get('operation_id')
   try:operation=db.get(BillingOperation,uuid.UUID(identifier)) if identifier else None
   except ValueError:operation=None
   if not operation or operation.workspace_id!=row.workspace_id or operation.provider_id!=current['id'] or current.get('customer')!=row.customer_id:raise HTTPException(400,'Checkout does not match its saved operation.')
   items=current.get('line_items',{}).get('data',[])
   if len(items)!=1 or items[0]['price']['id']!=operation.detail['price'] or items[0]['quantity']!=operation.quantity:raise HTTPException(400,'Checkout line items differ from the saved purchase.')
   if current.get('payment_status') in {'paid','no_payment_required'} and current.get('status')=='complete':
    operation.status='succeeded';operation.credits=float(operation.detail.get('credits',0))*operation.quantity
   if operation.kind=='subscription':row.subscription_id=current.get('subscription')
   refresh(db,row,client(request),settings)
  elif kind.startswith('customer.subscription.') or kind in {'invoice.paid','invoice.payment_failed'}:refresh(db,row,client(request),settings)
  elif kind.startswith('payment_intent.'):
   identifier=obj.get('metadata',{}).get('operation_id')
   try:operation=db.get(BillingOperation,uuid.UUID(identifier)) if identifier else None
   except ValueError:operation=None
   if operation and operation.workspace_id==row.workspace_id and operation.kind=='topup':
    current=client(request).request('GET','payment_intents/'+quote(obj['id'],safe=''));apply_intent(operation,row,current);operation.provider_id=current['id']
 db.commit();return {'received':True}


@router.get('/workspaces/{workspace_id}/workspace-settings/usage')
def report(workspace_id:uuid.UUID,request:Request,start:datetime,end:datetime,group:Literal['hour','day','week','month','quarter','year']='day',segment:Literal['interaction_type','source','teamspace','ai_agent','live_agent','team']='interaction_type',interaction_type:Optional[str]=None,source:Optional[str]=None,teamspace:Optional[str]=None,ai_agent:Optional[str]=None,live_agent:Optional[str]=None,team:Optional[str]=None,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'view_billing');return usage.aggregate(db,workspace_id,request.app.state.settings,start,end,group,segment,{'interaction_type':interaction_type,'source':source,'teamspace':teamspace,'ai_agent':ai_agent,'live_agent':live_agent,'team':team})


@router.get('/workspaces/{workspace_id}/workspace-settings/billing/usage-options')
def usage_options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'view_billing')
 return {'ai_agent':[{'id':str(a.id),'name':a.name} for a in db.scalars(select(Agent).where(Agent.workspace_id==workspace_id))],
  'live_agent':[{'id':str(u.id),'name':u.full_name or u.email} for u in db.scalars(select(User).join(Membership,Membership.user_id==User.id).where(Membership.workspace_id==workspace_id))],
  'teamspace':[{'id':str(t.id),'name':t.name} for t in db.scalars(select(Teamspace).where(Teamspace.workspace_id==workspace_id))],
  'team':[{'id':str(t.id),'name':t.name} for t in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False)))]}
