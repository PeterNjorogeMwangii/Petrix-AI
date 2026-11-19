"""Perception ACL, scheduling, collection, deduplication and report arithmetic."""
import json,uuid
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from sqlalchemy import select,func
import pytest
from fastapi import HTTPException
from app.core.security import utcnow
from app.models import (PerceptionSettings,PerceptionKeyword,PerceptionSource,PerceptionRun,PerceptionSourceKeyword,
    PerceptionEntry,Teamspace,Membership,WorkspaceRole,SupportTeam)
from app.perception import service as s,worker,reports
from app.perception.schemas import SourceBody,Range,page_url
from app.perception.collector import Page,PublicEntry,CollectionError,Collector
from app.teamspace.ai import AIResult
from test_agents import agent_workspace
from test_workspace_settings import ws,custom_actor

@pytest.fixture
def perception(client,ws,app):
 root,path,agent,h=ws;o=client.get(root+'/perception/options');assert o.status_code==200,o.text
 space=next(v for v in o.json()['teamspaces'] if v['is_default'])['id'];base=root+'/perception/'+space
 return {'root':root,'base':base,'space':space,'ws':uuid.UUID(root.split('/')[3]),'h':h,'app':app}

def post(client,f,path,body,status=200):
 r=client.post(f['base']+path,json=body,headers=f['h']);assert r.status_code==status,r.text;return r.json()
def keyword(client,f,name='JKIA cancellation',**overrides):return post(client,f,'/keywords',{'name':name,'variations':['JKIA','#JKIACancellation'],'enabled':True,**overrides},201)
def source(client,f,**kwargs):return post(client,f,'/sources',{'platform':'x','method':'page','name':'Public profile','url':'https://x.com/eclectics','timezone':'Africa/Nairobi',**kwargs},201)
def schedule_manual(client,f,src,start='2026-11-01',end='2026-11-12'):return post(client,f,'/sources/'+src['id']+'/scrape',{'start':start,'end':end},202)
def settings(client,f,**values):
 cfg=client.get(f['base']+'/settings').json()['config'];return post(client,f,'/settings',{**{k:v for k,v in cfg.items() if k!='revision'},'expected_revision':cfg['revision'],**values})
class FakeCollector:
 available=True
 def __init__(self,entries=None,pages=None):self.entries=entries or [];self.calls=[];self.pages=pages
 def collect(self,payload):
  self.calls.append(payload)
  return self.pages[len(self.calls)-1] if self.pages else Page(entries=self.entries,complete=True,public_source_verified=True)
def item(id='post1',content='JKIA cancellation is frustrating.',posted='2026-11-10T10:00:00Z',**kw):return PublicEntry(external_id=id,content=content,url='https://x.com/eclectics/status/'+id,posted_at=posted,**kw)


def test_options_empty_overview_settings_read_only_and_preview_contract(client,perception):
 f=perception;o=client.get(f['root']+'/perception/options').json()
 assert o['collector_ready'] is False and o['availability']['alerts']=='preview' and o['availability']['categorization']=='preview'
 assert [p['key'] for p in o['platforms'] if p['supported']]==['facebook','x']
 d=client.get(f['base']+'/overview').json();assert d['total']==0 and d['timezone']=='Africa/Nairobi'
 assert all(v['count']==0 and v['percent']==0 for v in d['summary'])
 with f['app'].state.session_factory() as db:assert db.scalar(select(func.count()).select_from(PerceptionSettings))==0
 assert client.get(f['base']+'/entries').json()['preview'] is True
 assert client.post(f['base']+'/alerts',json={},headers=f['h']).status_code==404


def test_keyword_crud_search_tags_revisions_and_csrf(client,perception):
 f=perception;g=keyword(client,f)
 assert client.post(f['base']+'/keywords',json={'name':'No CSRF'}).status_code==403
 assert client.get(f['base']+'/keywords',params={'q':'cancellation','status':'enabled'}).json()['total']==1
 assert client.get(f['base']+'/keywords',params={'q':'absent'}).json()['total']==0
 assert client.post(f['base']+'/keywords',json={'name':' JKIA CANCELLATION '},headers=f['h']).status_code==409
 bad={**g,'name':'Other'};body={k:bad[k] for k in ['name','variations','tag_ids','enabled']};body['expected_revision']=g['revision']
 edited=post(client,f,'/keywords/'+g['id'],body);assert edited['revision']==2
 assert client.post(f['base']+'/keywords/'+g['id'],json=body,headers=f['h']).status_code==409
 assert client.post(f['base']+'/keywords',json={'name':'Wrong tag','tag_ids':[str(uuid.uuid4())]},headers=f['h']).status_code==422
 assert client.get(f['base']+'/keywords',params={'q':'x'*257}).status_code==422


def test_view_and_manage_grants_are_independent_and_all_writes_checked(client,perception):
 f=perception;src=source(client,f);custom_actor(f['app'],str(f['ws']),['view_perception'])
 assert client.get(f['base']+'/sources').status_code==200
 assert client.get(f['base']+'/sources/'+src['id']).status_code==200
 assert client.get(f['root']+'/perception/options').json()['teamspaces'][0]['can_manage'] is False
 for path,body in [('/keywords',{'name':'Denied'}),('/sources',{'platform':'x','name':'Denied','url':'https://x.com/example'}),('/sources/'+src['id']+'/recurring',{'expected_revision':1,'recurring':True}),('/sources/'+src['id']+'/scrape',{'start':'2026-11-01','end':'2026-11-02'}),('/processing/retry',{})]:
  assert client.post(f['base']+path,json=body,headers=f['h']).status_code==403
 with f['app'].state.session_factory() as db:
  role=db.scalar(select(WorkspaceRole).where(WorkspaceRole.name=='Restricted'));role.permissions=['manage_perception'];db.commit()
 assert client.get(f['root']+'/perception/options').status_code==403
 assert client.get(f['base']+'/overview').status_code==403


def test_team_role_only_exposes_allowed_teamspace(client,perception):
 f=perception;second=post(client,{**f,'base':f['root']+'/workspace-settings'},'/teamspaces',{'name':'Private'},201)
 uid,_=custom_actor(f['app'],str(f['ws']),['view_perception','manage_perception'],'team')
 o=client.get(f['root']+'/perception/options').json();assert [v['id'] for v in o['teamspaces']]==[f['space']]
 assert client.get(f['root']+'/perception/'+second['id']+'/settings').status_code==403
 with f['app'].state.session_factory() as db:
  member=db.scalar(select(Membership).where(Membership.user_id==uid,Membership.workspace_id==f['ws']));member.teamspace_ids=[f['space']];db.commit()
 assert client.get(f['root']+'/perception/'+second['id']+'/overview').status_code==404


@pytest.mark.parametrize('url',['https://x.com/home','https://x.com/handle/status/1','https://x.com/handle/other','https://x.com/search?q=jkia','https://twitter.com/messages','https://x.com.example.com/name','http://x.com/name','https://user:pw@x.com/name','https://x.com:8080/name'])
def test_invalid_x_urls_rejected(url):
 with pytest.raises(ValueError,match='Invalid X'):page_url('x',url)

@pytest.mark.parametrize('url',['https://facebook.com/groups/123','https://facebook.com/name/posts/123','https://facebook.com/watch','https://facebook.com.example.com/name','https://facebook.com/','https://facebook.com/login'])
def test_invalid_facebook_urls_rejected(url):
 with pytest.raises(ValueError):page_url('facebook',url)


def test_valid_urls_supported_platforms_intervals_and_duplicate_groups(client,perception):
 f=perception;assert source(client,f,url='https://mobile.twitter.com/support_1')['url']=='https://x.com/support_1'
 assert source(client,f,platform='facebook',url='https://facebook.com/eclectics',name='FB')['url']=='https://www.facebook.com/eclectics'
 for platform in ['instagram','tiktok','youtube','news','app_store','play_store']:
  r=client.post(f['base']+'/sources',json={'platform':platform,'name':'No'},headers=f['h']);assert r.status_code==422 and 'Scraping is not supported for this platform yet.' in r.text
 for unit,value in [('days',0),('days',31),('hours',721),('minutes',9),('minutes',721)]:
  r=client.post(f['base']+'/sources',json={'platform':'x','name':'Bad','url':'https://x.com/support','interval_unit':unit,'interval_value':value},headers=f['h']);assert r.status_code==422
 g=keyword(client,f);src=source(client,f,method='keyword',url='',keyword_ids=[g['id']],name='Search')
 r=client.post(f['base']+'/sources',json={'platform':'x','method':'keyword','name':'Duplicate','keyword_ids':[g['id']]},headers=f['h']);assert r.status_code==409
 assert source(client,f,platform='facebook',method='keyword',url='',keyword_ids=[g['id']],name='Facebook Search')


def test_confirmation_auto_pause_and_keyword_pruning_on_next_scrape(client,perception):
 f=perception;g=keyword(client,f);src=source(client,f,method='keyword',url='',keyword_ids=[g['id']],recurring=True)
 body={'name':g['name'],'variations':g['variations'],'enabled':False,'expected_revision':g['revision']}
 r=client.post(f['base']+'/keywords/'+g['id'],json=body,headers=f['h']);assert r.status_code==409 and r.json()['detail']['affected_sources']==1
 post(client,f,'/keywords/'+g['id'],{**body,'confirmed':True})
 d=client.get(f['base']+'/sources/'+src['id']).json();assert not d['recurring'] and d['next_run_at'] is None
 assert any(l['event']=='recurring_auto_paused' for l in d['logs']['items'])
 assert d['keyword_ids']==[g['id']]
 assert client.post(f['base']+'/sources/'+src['id']+'/recurring',json={'expected_revision':d['revision'],'recurring':True},headers=f['h']).status_code==422
 # Another still-active group permits a manual run, which removes inactive links.
 other=keyword(client,f,'Airport disruption');d=client.get(f['base']+'/sources/'+src['id']).json()
 with f['app'].state.session_factory() as db:db.add(PerceptionSourceKeyword(source_id=uuid.UUID(src['id']),keyword_id=uuid.UUID(other['id']),teamspace_id=uuid.UUID(f['space']),platform='x'));db.commit()
 schedule_manual(client,f,src);job=worker.claim(f['app'].state.session_factory);assert job
 d=client.get(f['base']+'/sources/'+src['id']).json();assert d['keyword_ids']==[other['id']]


def test_global_toggle_and_bulk_require_confirmation(client,perception):
 f=perception;g=keyword(client,f);source(client,f,method='keyword',url='',keyword_ids=[g['id']],recurring=True)
 cfg=client.get(f['base']+'/settings').json()['config'];body={k:v for k,v in cfg.items() if k!='revision'};body.update(expected_revision=cfg['revision'],use_custom=False)
 assert client.post(f['base']+'/settings',json=body,headers=f['h']).status_code==409
 post(client,f,'/settings',{**body,'confirmed':True})
 k2=keyword(client,f,'Second');selection={'ids':[g['id'],k2['id']],'operation':'disable'}
 assert client.post(f['base']+'/keywords/bulk',json=selection,headers=f['h']).status_code==409
 post(client,f,'/keywords/bulk',{**selection,'confirmed':True})
 assert client.post(f['base']+'/keywords/bulk',json={'all_matching':True,'operation':'enable'},headers=f['h']).status_code==409
 r=post(client,f,'/keywords/bulk',{'all_matching':True,'operation':'enable','confirmed':True});assert r['affected']==2


def test_maximum_50_enabled_groups_rollback_and_inactive_groups_allowed(client,perception):
 f=perception
 with f['app'].state.session_factory() as db:
  for i in range(50):db.add(PerceptionKeyword(workspace_id=f['ws'],teamspace_id=uuid.UUID(f['space']),name='Key '+str(i),name_key='key '+str(i),enabled=True))
  db.commit()
 r=client.post(f['base']+'/keywords',json={'name':'Too many'},headers=f['h']);assert r.status_code==422
 assert client.get(f['base']+'/keywords').json()['total']==50
 assert keyword(client,f,'Disabled',enabled=False)['enabled'] is False


def test_team_keywords_follow_names_alternatives_and_archives_pause_sources(client,perception):
 f=perception;team=client.post(f['root']+'/teams',json={'name':'Team One','alternative_names':['Team A'],'member_ids':[]},headers=f['h']).json();g=client.get(f['base']+'/keywords?kind=team').json()['items'][0]
 assert g['revision']==0 and g['name']=='Team One' and g['variations']==['Team A']
 post(client,f,'/teams/'+team['id'],{'enabled':True,'expected_revision':0});settings(client,f,use_teams=True)
 src=source(client,f,method='keyword',url='',keyword_ids=[g['id']],recurring=True)
 with f['app'].state.session_factory() as db:
  t=db.get(SupportTeam,uuid.UUID(team['id']));t.name='Renamed';t.alternative_names=['Team B'];db.commit()
 assert client.get(f['base']+'/keywords?kind=team').json()['items'][0]['name']=='Renamed'
 with f['app'].state.session_factory() as db:db.get(SupportTeam,uuid.UUID(team['id'])).archived=True;db.commit()
 worker.tick(f['app'].state.session_factory,FakeCollector(),SimpleNamespace(available=False))
 d=client.get(f['base']+'/sources/'+src['id']).json();assert d['recurring'] is False and d['scope']==[]


def test_recurring_contiguous_ranges_delays_interval_change_and_retry_no_duplicate_successor(client,perception):
 f=perception;src=source(client,f,recurring=True,interval_unit='minutes',interval_value=10);factory=f['app'].state.session_factory
 with factory() as db:
  run=db.scalar(select(PerceptionRun).where(PerceptionRun.source_id==uuid.UUID(src['id']),PerceptionRun.event=='scrape'));start=run.range_start;end=run.range_end
 assert end-start==timedelta(minutes=10)
 job=worker.claim(factory,now=end+timedelta(minutes=25));assert job
 with factory() as db:
  runs=list(db.scalars(select(PerceptionRun).where(PerceptionRun.source_id==uuid.UUID(src['id']),PerceptionRun.event=='scrape').order_by(PerceptionRun.range_start)))
  assert len(runs)==2 and runs[1].range_start==end and runs[1].range_end==end+timedelta(minutes=10)
 worker.finish(factory,job,error='Test provider failure')
 post(client,f,'/sources/'+src['id']+'/retry/'+job['payload']['run_id'],{},202)
 retried=worker.claim(factory);assert retried;worker.finish(factory,retried,[])
 with factory() as db:assert db.scalar(select(func.count()).select_from(PerceptionRun).where(PerceptionRun.source_id==uuid.UUID(src['id']),PerceptionRun.event=='scrape'))==2
 d=client.get(f['base']+'/sources/'+src['id']).json();payload={k:d[k] for k in ['platform','method','name','url','keyword_ids','recurring','interval_unit','interval_value','timezone']};payload.update(expected_revision=d['revision'],interval_value=30)
 updated=post(client,f,'/sources/'+src['id'],payload);assert datetime.fromisoformat(updated['next_run_at'])>utcnow()+timedelta(minutes=29)


def test_historical_validation_whole_days_seconds_and_timezone(client,perception):
 f=perception;g=keyword(client,f);fb=source(client,f,platform='facebook',method='keyword',url='',keyword_ids=[g['id']])
 for start,end in [('2026-11-01T00:00:00','2026-11-02T00:00:00'),('2023-12-31','2024-01-02'),('2024-01-01','2026-01-01'),('2026-11-12','2026-11-01'),('2030-01-01','2030-01-02')]:
  r=client.post(f['base']+'/sources/'+fb['id']+'/scrape',json={'start':start,'end':end},headers=f['h']);assert r.status_code==422,r.text
 run=schedule_manual(client,f,fb,start='2026-11-01',end='2026-11-02');assert run['range_start'].startswith('2026-11-01T00:00:00') and run['range_end'].startswith('2026-11-03T00:00:00')
 x=source(client,f,name='Seconds');run=schedule_manual(client,f,x,start='2026-11-01T10:20:30',end='2026-11-01T10:21:31');assert (datetime.fromisoformat(run['range_end'])-datetime.fromisoformat(run['range_start'])).total_seconds()==61 and run['range_start'].startswith('2026-11-01T07:20:30')


def test_collection_counts_deduplicates_filters_irrelevant_posts_and_preserves_snapshots(client,perception):
 f=perception;g=keyword(client,f);src=source(client,f,method='keyword',url='',keyword_ids=[g['id']]);schedule_manual(client,f,src)
 collector=FakeCollector([item(),item(),item('unrelated',content='Other topic'),item('reply',kind='reply'),item('old',posted='2024-01-01T00:00:00Z')]);factory=f['app'].state.session_factory
 assert worker.run_one(factory,collector)
 d=client.get(f['base']+'/sources/'+src['id']).json();run=next(r for r in d['logs']['items'] if r['event']=='scrape');assert run['status']=='success' and run['detail']['new']==1 and run['detail']['duplicates']==1
 raw=client.get(f['base']+'/entries',params={'start':'2026-11-01','end':'2026-11-12'}).json();assert raw['total']==1 and raw['items'][0]['keywords']=={g['id']:g['name']}
 schedule_manual(client,f,src);worker.run_one(factory,collector)
 assert client.get(f['base']+'/keywords').json()['items'][0]['scraped_data']==1
 post(client,f,'/sources/'+src['id']+'/delete',{'expected_revision':src['revision'],'confirmed':True})
 raw=client.get(f['base']+'/entries',params={'start':'2026-11-01','end':'2026-11-12'}).json();assert raw['total']==1 and raw['items'][0]['sources'][src['id']]=='Public profile'


def test_cross_source_dedup_retains_both_provenances(client,perception):
 f=perception;factory=f['app'].state.session_factory;src1=source(client,f,name='One');src2=source(client,f,name='Two')
 for src in [src1,src2]:schedule_manual(client,f,src);worker.run_one(factory,FakeCollector([item()]))
 raw=client.get(f['base']+'/entries',params={'start':'2026-11-01','end':'2026-11-12'}).json();assert raw['total']==1 and set(raw['items'][0]['sources'])=={src1['id'],src2['id']}


def test_missing_provider_failed_runs_punctuation_query_and_deleted_source_fence(client,perception):
 f=perception;src=source(client,f);schedule_manual(client,f,src);factory=f['app'].state.session_factory
 worker.run_one(factory,Collector(f['app'].state.settings));d=client.get(f['base']+'/sources/'+src['id']).json();assert d['manual']['status']=='failed' and 'not configured' in d['manual']['error']
 g=keyword(client,f,'!!!',variations=['###']);search=source(client,f,method='keyword',url='',keyword_ids=[g['id']],name='Punctuation');schedule_manual(client,f,search);fake=FakeCollector();worker.run_one(factory,fake);assert fake.calls==[]
 assert client.get(f['base']+'/sources/'+search['id']).json()['manual']['status']=='failed'
 post(client,f,'/sources/'+src['id']+'/retry/'+d['manual']['id'],{},202);job=worker.claim(factory);post(client,f,'/sources/'+src['id']+'/delete',{'expected_revision':src['revision'],'confirmed':True});assert worker.finish(factory,job,[item()]) is False


def test_collection_invalid_page_and_page_budget_never_save_partial_entries(client,perception):
 f=perception;src=source(client,f);schedule_manual(client,f,src);factory=f['app'].state.session_factory
 pages=[Page(entries=[item(str(i))],complete=False,next_cursor=str(i),public_source_verified=True) for i in range(10)]
 worker.run_one(factory,FakeCollector(pages=pages))
 d=client.get(f['base']+'/sources/'+src['id']).json();assert d['manual']['status']=='failed' and '10 collection pages' in d['manual']['error']
 with factory() as db:assert db.scalar(select(func.count()).select_from(PerceptionEntry))==0


def test_overview_unique_counts_independent_comparisons_relative_and_timezone(client,perception):
 f=perception;g=keyword(client,f);src=source(client,f);schedule_manual(client,f,src);factory=f['app'].state.session_factory
 worker.run_one(factory,FakeCollector([item(sentiment='negative'),item('p2',content='Great JKIA support',sentiment='positive'),item('boundary',posted='2026-11-09T22:00:00Z',sentiment='neutral')]))
 controls={'main':{'filters':{'sentiments':['negative']}},'left':{'segment_by':'sentiment','mode':'relative'},'right':{'segment_by':'keyword','filters':{'sentiments':['positive']}}}
 d=client.get(f['base']+'/overview',params={'start':'2026-11-10','end':'2026-11-10','charts':json.dumps(controls)}).json()
 assert d['total']==3 and d['summary'][0]['count']==3 and d['summary'][0]['percent']==100
 assert d['charts']['main']['matched_entries']==1 and d['charts']['left']['matched_entries']==3 and d['charts']['right']['matched_entries']==1
 assert sum(v['y'] for v in d['charts']['left']['data'])==pytest.approx(100,abs=.01)
 assert d['top_keywords']['items'][0]['count']==3
 assert client.get(f['base']+'/overview',params={'start':'2026-11-01','end':'2026-11-12','charts':'{"main":{"group_by":"hour"}}'}).status_code==422
 assert client.get(f['base']+'/entries',params={'start':'2026-11-10','end':'2026-11-10','filters':json.dumps({'sentiments':['negative']}),'limit':1}).json()['total']==1


def test_translation_exclusions_retains_original_and_retries_failures(client,perception):
 f=perception;settings(client,f,translate=True,target_language='en',excluded_languages=['fr']);src=source(client,f);schedule_manual(client,f,src);factory=f['app'].state.session_factory
 worker.run_one(factory,FakeCollector([item('sw',content='Huduma nzuri',language='sw'),item('fr',content='Merci',language='fr',sentiment='positive'),item('en',content='Thanks',language='en',sentiment='positive')]))
 class AI:
  available=True
  def generate(self,system,content):return AIResult(True,text=json.dumps({'language':'sw','sentiment':'positive','translation':'Good service'}))
 for _ in range(3):worker.process_one(factory,AI())
 with factory() as db:
  rows={r.language:r for r in db.scalars(select(PerceptionEntry))};assert rows['sw'].translation=='Good service' and rows['sw'].content=='Huduma nzuri' and rows['fr'].translation=='' and rows['en'].translation==''
 schedule_manual(client,f,src);worker.run_one(factory,FakeCollector([item('sw2',content='Huduma mbaya',language='sw')]))
 worker.process_one(factory,SimpleNamespace(available=False))
 assert client.get(f['base']+'/settings').json()['processing']['failed']==1
 f['app'].state.support_ai=AI();assert post(client,f,'/processing/retry',{})['queued']==1
 worker.process_one(factory,AI())
 assert client.get(f['base']+'/settings').json()['processing'].get('failed',0)==0


def test_provider_payload_has_bounded_verified_public_contract_and_no_user_endpoint(client,perception):
 f=perception;src=source(client,f,platform='facebook',url='https://facebook.com/eclectics');schedule_manual(client,f,src);factory=f['app'].state.session_factory;fake=FakeCollector()
 worker.run_one(factory,fake);p=fake.calls[0];assert p['platform']=='facebook' and p['include_comments'] is True and p['include_replies'] is False and p['page_size']==500
 assert p['range_start'] and p['range_end'] and p['run_id'] and p['url']=='https://www.facebook.com/eclectics'


def test_provider_http_auth_verification_pagination_and_error_redaction(monkeypatch):
 import httpx
 from app.core.config import Settings
 import app.perception.collector as module
 original=httpx.Client;captured=[]
 settings=Settings(_env_file=None,app_env='test',database_url='postgresql+psycopg://u:p@localhost/db',session_signing_secret='test-only-secret-more-than-32-characters',perception_provider_url='https://collection.example/collect',perception_provider_token='SECRET-NOT-FOR-ERRORS')
 response={'entries':[],'complete':True,'public_source_verified':True}
 def transport(request):captured.append(request);return httpx.Response(200,json=response)
 monkeypatch.setattr(module.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(transport),**kw))
 c=Collector(settings);p={'version':1,'run_id':str(uuid.uuid4()),'page':0};assert c.collect(p).complete
 assert captured[0].headers['authorization']=='Bearer SECRET-NOT-FOR-ERRORS' and captured[0].headers['idempotency-key'].endswith(':0')
 response['public_source_verified']=False
 with pytest.raises(CollectionError,match='could not verify'):c.collect(p)
 response.update(public_source_verified=True,complete=False,next_cursor=None)
 with pytest.raises(CollectionError,match='omitted'):c.collect(p)
 response.update(complete=True,next_cursor='bad')
 with pytest.raises(CollectionError,match='inconsistent'):c.collect(p)
 monkeypatch.setattr(module.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(lambda r:httpx.Response(401,text='SECRET-NOT-FOR-ERRORS')),**kw))
 with pytest.raises(CollectionError) as e:c.collect(p)
 assert 'SECRET' not in str(e.value)


@pytest.mark.parametrize('url',['https://user:secret@collector.example/post','https://collector.example/post?secret=1','https://collector.example/post#fragment','http://collector.example/post','file:///tmp/data'])
def test_provider_endpoint_configuration_rejects_credentials_and_unsafe_urls(url):
 from app.core.config import Settings
 with pytest.raises(ValueError):Settings(_env_file=None,database_url='postgresql+psycopg://u:p@localhost/db',session_signing_secret='test-only-secret-more-than-32-characters',perception_provider_url=url)


def test_expired_collection_worker_cannot_save_late_entries(client,perception):
 f=perception;src=source(client,f);schedule_manual(client,f,src);factory=f['app'].state.session_factory;job=worker.claim(factory);assert job
 assert worker.claim(factory) is None
 with factory() as db:db.get(PerceptionRun,uuid.UUID(job['payload']['run_id'])).lease_until=utcnow()-timedelta(seconds=1);db.commit()
 assert worker.claim(factory) is None
 assert worker.finish(factory,job,[item()]) is False
 d=client.get(f['base']+'/sources/'+src['id']).json();assert d['manual']['status']=='failed' and 'interrupted' in d['manual']['error']


def test_source_date_ranges_handle_dst_and_calendar_year_maximum():
 source=SimpleNamespace(platform='x',method='page',timezone='America/New_York')
 now=datetime(2027,1,1,tzinfo=timezone.utc)
 for start,end in [('2026-03-08T02:30:00','2026-03-08T03:45:00'),('2026-11-01T01:30:00','2026-11-01T02:45:00')]:
  with pytest.raises(HTTPException):s.range_values(source,Range(start=start,end=end),now)
 a,b=s.range_values(source,Range(start='2026-11-01T01:30:00-04:00',end='2026-11-01T01:30:00-05:00'),now);assert b-a==timedelta(hours=1)
 with pytest.raises(HTTPException):s.range_values(source,Range(start='2025-01-01',end='2026-01-01'),now)
 a,b=s.range_values(source,Range(start='2025-01-01',end='2025-12-31'),now);assert (b-a).days==365


def test_facebook_page_breakdown_has_posts_and_comments(client,perception):
 f=perception;src=source(client,f,platform='facebook',url='https://facebook.com/eclectics');schedule_manual(client,f,src)
 posts=[PublicEntry(external_id='fb1',content='A public post',url='https://facebook.com/eclectics/posts/1',posted_at='2026-11-10T10:00:00Z'),PublicEntry(external_id='fb2',content='A public comment',url='https://facebook.com/eclectics/posts/1?comment_id=2',posted_at='2026-11-10T11:00:00Z',kind='comment')]
 worker.run_one(f['app'].state.session_factory,FakeCollector(posts));d=client.get(f['base']+'/sources/'+src['id']).json();results=d['manual']['detail']['results'];assert {r['label']:r['new'] for r in results}=={'Posts':1,'Comments':1}


def test_auto_status_shows_last_execution_instead_of_hiding_behind_next_schedule(client,perception):
 f=perception;src=source(client,f,recurring=True,interval_unit='minutes',interval_value=10);factory=f['app'].state.session_factory
 with factory() as db:
  row=db.scalar(select(PerceptionRun).where(PerceptionRun.source_id==uuid.UUID(src['id']),PerceptionRun.trigger=='recurring'));due=row.scheduled_at
 job=worker.claim(factory,now=due);worker.finish(factory,job,[])
 d=client.get(f['base']+'/sources/'+src['id']).json();assert d['auto']['status']=='success' and d['next_run_at'] is not None
 assert any(r['status']=='scheduled' for r in d['logs']['items'])


def test_paused_keyword_source_can_be_renamed_after_its_last_keyword_is_removed(client,perception):
 f=perception;g=keyword(client,f);src=source(client,f,method='keyword',url='',keyword_ids=[g['id']],recurring=False)
 post(client,f,'/keywords/bulk',{'ids':[g['id']],'operation':'delete','confirmed':True})
 d=client.get(f['base']+'/sources/'+src['id']).json();assert d['keyword_ids']==[]
 body={k:d[k] for k in ['platform','method','name','url','keyword_ids','recurring','interval_unit','interval_value','timezone']};body.update(expected_revision=d['revision'],name='Paused source retained')
 edited=post(client,f,'/sources/'+src['id'],body);assert edited['name']=='Paused source retained' and not edited['recurring']
 body.update(expected_revision=edited['revision'],recurring=True)
 assert client.post(f['base']+'/sources/'+src['id'],json=body,headers=f['h']).status_code==422


def test_same_named_custom_and_team_groups_keep_separate_scrape_result_rows(client,perception):
 f=perception;g=keyword(client,f,'JKIA');team=client.post(f['root']+'/teams',json={'name':'JKIA','alternative_names':[],'member_ids':[]},headers=f['h']).json();t=client.get(f['base']+'/keywords?kind=team').json()['items'][0]
 post(client,f,'/teams/'+team['id'],{'enabled':True,'expected_revision':0});settings(client,f,use_teams=True)
 src=source(client,f,method='keyword',url='',keyword_ids=[g['id'],t['id']]);schedule_manual(client,f,src);worker.run_one(f['app'].state.session_factory,FakeCollector([item()]))
 results=client.get(f['base']+'/sources/'+src['id']).json()['manual']['detail']['results'];assert len(results)==2 and {r['keyword_id'] for r in results}=={g['id'],t['id']} and all(r['new']==1 for r in results)
