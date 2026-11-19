"""Reporting uses publication dates in the workspace timezone and counts unique entries."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from typing import Literal,List
from pydantic import Field
from sqlalchemy import select,func
from sqlalchemy.orm import load_only
from fastapi import HTTPException
from app.agent_schemas import StrictModel
from app.analytics.data import bounds,zone
from app.models import PerceptionEntry as Entry,PerceptionSource as Source
from app.perception.schemas import Filters,PLATFORMS

class Chart(StrictModel):
    group_by:Literal['hour','day','week','month','quarter','year']='day'
    segment_by:Literal['none','source','platform','source_type','sentiment','keyword','category']='platform'
    mode:Literal['actual','relative']='actual'
    filters:Filters=Field(default_factory=Filters)
class Charts(StrictModel):
    main:Chart=Field(default_factory=Chart)
    left:Chart=Field(default_factory=lambda:Chart(segment_by='sentiment'))
    right:Chart=Field(default_factory=lambda:Chart(segment_by='keyword'))

def category(platform):return 'App stores' if platform in {'app_store','play_store'} else 'News' if platform=='news' else 'Social media'
def sentiment(v):return 'Not scored' if v=='unknown' else v.title()
def matches(row,f):
    return (not f.sources or set(map(str,f.sources))&row.sources.keys()) and (not f.platforms or row.platform in f.platforms) and (not f.sentiments or row.sentiment in f.sentiments) and (not f.keywords or set(map(str,f.keywords))&row.keywords.keys())

def rows(db,workspace,space,start,end):
    first,last,lower,upper,tz=bounds(db,workspace,start,end)
    query=select(Entry).where(Entry.workspace_id==workspace,Entry.teamspace_id==space,Entry.posted_at>=lower,Entry.posted_at<upper).options(load_only(Entry.id,Entry.platform,Entry.posted_at,Entry.sources,Entry.keywords,Entry.categories,Entry.sentiment))
    return list(db.scalars(query)),first,last,tz

def bucket(dt,unit,tz):
    dt=dt.astimezone(tz)
    if unit=='hour':return dt.replace(minute=0,second=0,microsecond=0).astimezone(timezone.utc)
    dt=dt.replace(hour=0,minute=0,second=0,microsecond=0)
    if unit=='week':dt-=timedelta(days=dt.weekday())
    if unit=='month':dt=dt.replace(day=1)
    if unit=='quarter':dt=dt.replace(day=1,month=1+3*((dt.month-1)//3))
    if unit=='year':dt=dt.replace(month=1,day=1)
    return dt.astimezone(timezone.utc)

def label(dt,unit,tz):
    dt=dt.astimezone(tz)
    return dt.strftime('%d %b %Y %H:%M %z') if unit=='hour' else str(dt.year) if unit=='year' else str(dt.year)+' Q'+str(1+(dt.month-1)//3) if unit=='quarter' else dt.strftime('%b %Y') if unit=='month' else dt.strftime('%d %b %Y')

def segments(row,dimension):
    if dimension=='source':return list(set(row.sources.values()))
    if dimension=='keyword':return list(set(row.keywords.values()))
    if dimension=='category':return list(set(row.categories))
    if dimension=='platform':return [PLATFORMS[row.platform]]
    if dimension=='source_type':return [category(row.platform)]
    if dimension=='sentiment':return [sentiment(row.sentiment)]
    return ['Entries']


def chart(data,first,last,tz,c,title):
    if c.group_by=='hour' and (last-first).days>6:raise HTTPException(422,'Choose at most 7 days when grouping Perception by hour.')
    selected=[r for r in data if matches(r,c.filters)];counter=Counter();series=Counter();keys=set()
    for row in selected:
        at=bucket(row.posted_at,c.group_by,tz);keys.add(at)
        for seg in segments(row,c.segment_by):counter[(at,seg)]+=1;series[seg]+=1
    # Fixed top series, retaining every other value in Other, keeps large charts readable.
    allowed={v for v,_ in series.most_common(12)};collapsed=Counter()
    for (at,seg),count in counter.items():collapsed[(at,seg if seg in allowed else 'Other')]+=count
    names=sorted({v[1] for v in collapsed});points=[]
    # Empty time buckets remain explicit, including repeated DST hours.
    from datetime import time
    start=datetime.combine(first,time.min,tz).astimezone(timezone.utc);end=datetime.combine(last+timedelta(days=1),time.min,tz).astimezone(timezone.utc)
    cursor=start
    while cursor<end:
        keys.add(bucket(cursor,c.group_by,tz));cursor+=timedelta(hours=1 if c.group_by=='hour' else 12)
    for at in sorted(keys):
        total=sum(collapsed[(at,n)] for n in names)
        for name in names:
            count=collapsed[(at,name)];points.append({'x':label(at,c.group_by,tz),'y':round(100*count/total,3) if c.mode=='relative' and total else count,'series':name})
    return {'title':title,'data':points,'unit':'Percent of mentions' if c.mode=='relative' else 'Entries','matched_entries':len(selected),'note':'An entry can mention multiple keywords or categories. Relative values show each series’ share of the bucket. Other retains series outside the top 12.'}

def overview(db,workspace,space,start,end,charts):
    data,first,last,tz=rows(db,workspace,space,start,end);totals=Counter(category(r.platform) for r in data);count=len(data)
    def top(dimension):
        values=Counter((PLATFORMS[r.platform],v) for r in data for v in segments(r,dimension))
        return {'items':[{'platform':a,'value':b,'count':n} for (a,b),n in sorted(values.items(),key=lambda v:(-v[1],v[0]))[:10]],'total':len(values)}
    return {'start':first,'end':last,'timezone':str(tz),'total':count,'summary':[{'key':k,'label':v,'count':totals[k],'percent':round(100*totals[k]/count,2) if count else 0} for k,v in [('Social media','Social media posts'),('App stores','App store reviews'),('News','News articles')]],
        'top_keywords':top('keyword'),'top_categories':top('category'),'top_sentiment':top('sentiment'),'charts':{k:chart(data,first,last,tz,getattr(charts,k),title) for k,title in [('main','Data breakdown'),('left','Comparison A'),('right','Comparison B')]}}

def entries(db,workspace,space,start,end,f,offset,limit):
    data,first,last,tz=rows(db,workspace,space,start,end);ids=[r.id for r in data if matches(r,f)]
    selected=list(db.scalars(select(Entry).where(Entry.id.in_(ids)).order_by(Entry.posted_at.desc(),Entry.id).offset(offset).limit(limit)))
    return {'preview':True,'items':[{k:getattr(r,k) for k in ['id','platform','sources','content','translation','language','posted_at','scraped_at','keywords','tag_ids','url','sentiment','processing','processing_error']} for r in selected],'total':len(ids),'offset':offset,'limit':limit,'timezone':str(tz)}
