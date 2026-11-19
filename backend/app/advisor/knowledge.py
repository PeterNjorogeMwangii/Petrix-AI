"""A packaged current-release guide plus bounded retrieval from public manuals."""
import json,math,re
from collections import Counter
from functools import lru_cache
from pathlib import Path

STOP=set('a an and are as at be can do does for from how i in is it me my of on or our please that the their then there these this to use using was we what when where which why with you your'.split())

def tokens(text):return [v for v in re.findall(r'[\w]+',text.casefold()) if len(v)>1 and v not in STOP]

@lru_cache(maxsize=1)
def manual():
    value=json.loads(Path(__file__).with_name('manual.json').read_text(encoding='utf-8'))
    return value

@lru_cache(maxsize=1)
def chunks():
    out=[]
    for slug,doc in manual()['documents'].items():
        # Historical install/version paragraphs are available in the viewer, but not used as current instructions.
        heading=doc['title'];skip=False
        for section in re.split(r'(?=^#{1,3} )',doc['content'],flags=re.M):
            first=section.split('\n',1)[0]
            if first.startswith('#'):
                heading=re.sub(r'^#+\s*','',first);skip=bool(re.search(r'upgrade|install|migration|release history',heading,re.I)) and slug!='PLATFORM_GUIDE'
            if skip:continue
            section='\n'.join(line for line in section.splitlines() if not re.match(r'Version `|Milestone |The current revision|.*schema head|.*migration check must',line,re.I))
            for start in range(0,len(section),1100):
                text=section[start:start+1400].strip()
                if len(text)<60:continue
                out.append({'id':slug+':'+str(len(out)),'slug':slug,'title':doc['title'],'heading':heading,'text':text,'terms':Counter(tokens(heading+' '+text))})
    return out

def retrieve(question,history=None):
    query=tokens(question)
    if (not query or set(query)<={'more','detail','details','next','example','examples','save','continue','same','one','another','else'}) and history:
        earlier=[m['content'] for m in history if m['role']=='user' and tokens(m['content'])]
        query+=tokens(' '.join(earlier[-2:]))
    keys=set(query);ranked=[];all_chunks=chunks()
    for c in all_chunks:
        matches=keys&c['terms'].keys()
        if not matches:continue
        score=sum((1+math.log(1+c['terms'][term])) for term in matches)/math.sqrt(1+len(c['terms'])/90)
        if c['slug']=='PLATFORM_GUIDE':score*=1.8
        score+=2*len(keys&set(tokens(c['heading'])))
        ranked.append((score,len(matches),c))
    ranked.sort(key=lambda v:(-v[0],v[2]['id']))
    if not ranked or ranked[0][1]<min(2,len(keys)) or not keys:return []
    result=[];per_doc=Counter()
    for score,_,c in ranked:
        if score<max(1,ranked[0][0]*.22) or per_doc[c['slug']]>=2:continue
        result.append({k:c[k] for k in ['id','slug','title','heading','text']});per_doc[c['slug']]+=1
        if len(result)==7:break
    return result

def file_passages(text,question,budget=7000):
    parts=[text[i:i+1500] for i in range(0,len(text),1300)];query=set(tokens(question))
    ranked=sorted(enumerate(parts),key=lambda item:(-len(query&set(tokens(item[1]))),item[0]))
    picked=[];size=0
    for index,part in ranked:
        if size+len(part)>budget:continue
        picked.append((index,part));size+=len(part)
        if size>=budget-1500:break
    return '\n\n'.join(p for _,p in sorted(picked))

TOPICS=[
 {'key':'test','terms':{'test','tester','testing'},'answer':'To test an AI agent:\n\n1. Open **AI agents** and select your agent.\n2. Save your settings and workflow changes.\n3. Use the conversation tester on the right. On a narrow screen, open the chat/tester button.\n4. Start or restart the chat and send your test question.\n5. Review the returned messages and execution trace. Use **Test an event** to simulate other supported events.\n\nGenerative replies need the configured model connection. Workflow message actions can be tested without it.','doc':'AI_AGENTS','nav':['agents']},
 {'key':'teamspace','terms':{'teamspace','teamspaces'},'answer':'A **teamspace** is an area inside a workspace that scopes access, support operations and records. A workspace can have several teamspaces, with **Default** created initially.\n\nOpen **Settings → Workspace → Teamspaces** to manage teamspaces. User membership and team roles determine which spaces and features each user can access. Some definitions, such as custom-field keys, remain workspace-wide.','doc':'WORKSPACE_SETTINGS','nav':['teamspaces']},
 {'key':'fields','terms':{'custom','field','fields'},'answer':'To add a custom field:\n\n1. Open **Settings → Teamspace → Custom fields**.\n2. Choose **Conversation fields** or **People fields**.\n3. Select **Create new field**.\n4. Configure the name, stable key, type, options and default value as appropriate, then save.\n\nYou need **Manage custom fields**. Type and category are fixed after creation; editable record values are separate from field definitions.','doc':'CUSTOM_FIELDS','nav':['fields']},
 {'key':'bans','terms':{'ban','bans','banning','offence','offenses'},'answer':'Yes. Configure ban rules and reasons in **Settings → Teamspace → Banning**, then use the authorized record tools to ban a person. Rules choose a duration based on previous offences; temporary bans expire automatically. Removing a ban retains offence history.\n\nViewing and managing bans require their respective permissions. Active bans prevent further customer/support replies and claiming the affected conversation.','doc':'TEAMSPACE_SETTINGS','nav':['banning']},
 {'key':'members','terms':{'member','members','invite','invitation','user','users'},'answer':'To add team members:\n\n1. Open **Settings → Workspace → Users**.\n2. Add an existing eligible account or send an invitation using the available user controls.\n3. Assign the appropriate role and teamspace access.\n4. Open **Settings → Workspace → Teams** to configure team membership and team roles.\n\nManaging users and managing teams are separate permissions.','doc':'WORKSPACE_SETTINGS','nav':['users','teams']},
 {'key':'knowledge','terms':{'knowledge','pdf','document','documents','fallback','intent','intents'},'answer':'Open **AI agents → Knowledge** to upload a readable PDF, add a public URL, or paste a snippet. Wait for training to succeed before testing.\n\nA workflow that matches and executes a fixed **Send message** still controls that reply. Uploaded Knowledge does not replace a matched workflow message. General questions need a generative/fallback route that reaches Knowledge retrieval, a working model connection, and the agent LLM setting enabled. Avoid a broad catch-all intent that always sends a fixed response.\n\nFor workflow LLM Processing, use the supported Knowledge configuration described in its guide.','doc':'KNOWLEDGE','nav':['agents']},
 {'key':'aliases','terms':{'alias','aliases','persona','signature'},'answer':'Open **Settings → My profile → Aliases**. Create an alias with a display name, avatar and optional email signature, then save. You can set one as the default for new conversations and choose an alias from the Inbox reply editor. Existing sent messages retain their original identity snapshots.','doc':'ACCOUNT_ALIASES','nav':['aliases']},
 {'key':'analytics','terms':{'analytics','report','reports','scroll','csat','heatmap'},'answer':'Open **Analytics** to choose Live report, Messaging, Chats, CSAT, Tickets or Live agents. Set the reporting period and filters, and use the report pane to scroll through charts and the tables below them. CSV/PNG controls download the displayed chart data or image.\n\nDates use the workspace timezone. Your role determines which reports and underlying record links are available; an aggregate report grant does not automatically grant access to every conversation.','doc':'ANALYTICS','nav':['analytics']},
 {'key':'perception','terms':{'perception','scrape','scraping','facebook','twitter','monitoring'},'answer':'Perception provides Overview charts, custom/team keywords, Facebook/X sources, durable scrape schedules and logs, and Translation. Public collection requires a configured collection service with platform access; translation uses the configured AI service.\n\nRaw Data is a read-only preview. Alerts and Categorization are not released in this build. View and Manage Perception are independent permissions.','doc':'PERCEPTION','nav':['perception']},
 {'key':'whatsapp','terms':{'whatsapp','meta','webhook','bsuid'},'answer':'Open **AI agents → Publish → Add Channel → WhatsApp**. Configure Basics and save the Meta App ID, App secret, system User access token and Phone number ID under **Configuration**. Select **Save & verify**. In **Webhook/Setup**, copy the callback URL and separate Petrix verify token into Meta, verify the callback and subscribe to **messages**. Refresh status, enable the channel and save. Publishing requires **Lite or above** and a public HTTPS API address. Normal replies require an open 24-hour customer service window. Human Handoff reaches native Inbox; Activity provides delivery status and guarded recovery. Meta credentials remain private.','doc':'WHATSAPP','nav':['agents']},
 {'key':'publish','terms':{'publish','publishing','published'},'answer':'Open **AI agents → Publish**, add or select a **Webchat** channel, configure its appearance and pre-chat form, and use **Preview** to test it. Save and enable the channel, then copy the installation snippet to your website. A public HTTPS API address is required for an external website. Saved agent settings and workflows are captured when the channel is saved; existing chats keep their workflow version. WhatsApp is also available on Lite or above: create a WhatsApp channel, verify the Meta credentials, configure its callback and messages subscription, then enable and save. See the WhatsApp guide for token setup and delivery recovery.','doc':'PLATFORM_GUIDE','nav':['agents']},
 {'key':'inbox','terms':{'inbox','takeover','transfer','copilot','reply','replies'},'answer':'Open **Inbox** to work with your permitted chats and tickets. Choose a view and conversation, then use its reply editor, notes and record tools. Claim or take over a conversation when required before sending a human reply. Copilot suggestions remain private until you insert and send them.\n\nLivechat and ticket access, transfer, and People-field permissions are checked separately.','doc':'INBOX','nav':['inbox']},
 {'key':'account','terms':{'profile','account','theme','online','away','offline','notifications'},'answer':'Open **Settings → My profile → Account** to edit your profile, language and notification preferences. The **Your account** menu controls Online/Away/Offline presence and System/Light/Dark theme, and lists the available workspace-switching action. Changing your displayed presence does not grant additional record access.','doc':'ACCOUNT_ALIASES','nav':['account']}
]

def topic(question,history):
    terms=set(tokens(question));ranked=[]
    if not terms or terms<={'more','detail','details','next','example','examples','save','continue','same','one','another','else'}:
        for item in history[-4:]:
            if item['role']=='user':terms.update(tokens(item['content']))
    for index,t in enumerate(TOPICS):
        score=len(terms&t['terms'])
        if t['key']=='fields' and not ({'field','fields'}&terms):continue
        if t['key']=='teamspace' and ('member' in terms or 'members' in terms) and 'teamspace' not in terms:continue
        if score:ranked.append((score,-index,t))
    return max(ranked,key=lambda x:x[:2])[2] if ranked else None
