"""Portable local workflow/PDF additions to the existing agent JSON import."""
import base64,json,uuid
from fastapi import HTTPException
from pydantic import ValidationError
from app.models import AgentKnowledgeSource,AgentChange
from app.core.security import utcnow
from app.agent_knowledge.jobs import parse_document
from app.agent_knowledge.extract import ImportFailure
from app.agent_knowledge.service import set_text
from app.agent_knowledge.schemas import MAX_FILE_BYTES
from app.workflows.defaults import SYSTEM_TRIGGERS,node_id
from app.workflows.numbering import walk,assign_numbers
from app.workflows.schemas import SaveWorkflows
from app.workflows.actions.registry import config_for
from app.workflows.service import get_document,validate_nodes

PORTABLE_ACTIONS={'send_message','ask_survey','set_variable','branch','jump','switch_language','llm_processing','close_chat','collect_feedback'}

def prepare_files(package):
    result=[]
    for item in package.knowledge_files:
        try:
            data=base64.b64decode(item.data_base64,validate=True)
            if not data or len(data)>MAX_FILE_BYTES or not data.startswith(b'%PDF-'):
                raise ValueError()
            text=parse_document(data,item.filename)
        except (ValueError,ImportFailure):
            raise HTTPException(422,"An embedded Knowledge PDF could not be read. Use a non-encrypted PDF with selectable text, up to 10 MB.") from None
        result.append((item,data,text))
    return result


def rebind_workflows(value,agent_id):
    try:
        parsed=SaveWorkflows.model_validate({'expected_revision':1,'nodes':value.nodes})
    except ValidationError:
        raise HTTPException(422,'The imported workflow structure is invalid.') from None
    nodes=[n.model_dump(mode='json') for n in parsed.nodes]
    if len(json.dumps(nodes,ensure_ascii=False).encode())>1024*1024:
        raise HTTPException(422,'Keep imported workflows below 1 MB.')
    ids={}
    for node in walk(nodes):
        if node['kind']=='action' and node['type'] not in PORTABLE_ACTIONS:
            raise HTTPException(422,'Portable workflow imports support local message, survey, variable, branch, jump, language, LLM Processing, closure and feedback actions. Configure workspace connections and media after importing.')
        if node['kind']=='action':
            try:
                # Canonical UUID strings and typed outcome structures before rebinding.
                node['config']=config_for(node['type']).model_validate(node['config']).model_dump(mode='json')
            except ValidationError:
                raise HTTPException(422,'The imported workflow action settings are invalid.') from None
        ids.setdefault(node['id'],str(uuid.uuid4()))
        for path in node.get('paths',[]):ids.setdefault(path['id'],str(uuid.uuid4()))
    for root in nodes:
        if root['kind']=='trigger' and root['type'] in SYSTEM_TRIGGERS:
            ids[root['id']]=node_id(agent_id,root['type'])
    for node in walk(nodes):
        original=node['id'];node['id']=ids[original];node.pop('number',None)
        for path in node.get('paths',[]):path['id']=ids[path['id']]
        for key,item in list(node['config'].items()):
            if key=='target_node_id' or key.endswith('_path_id'):
                if item in ids:node['config'][key]=ids[item]
        if node['type']=='branch':
            for branch in node['config']['conditions']:
                if branch.get('id') in ids:branch['id']=ids[branch['id']]
    return validate_nodes(nodes,agent_id)


def apply_bundle(db,agent,actor_id,package,files):
    if package.workflows is not None:
        nodes=rebind_workflows(package.workflows,agent.id)
        from app.workflows.actions.switch_language.validation import validate_targets
        validate_targets(nodes,agent.languages)
        document=get_document(db,agent.id,lock=True)
        document.next_node_number=assign_numbers(nodes,document.nodes,document.next_node_number)
        document.nodes=nodes;document.revision+=1;document.updated_at=utcnow()
        db.add(AgentChange(workspace_id=agent.workspace_id,agent_id=agent.id,agent_name=agent.name,actor_id=actor_id,action='workflows_imported',summary='Portable workflows imported with new agent-local identities.'))
    for item,data,text in files:
        row=AgentKnowledgeSource(agent_id=agent.id,id=uuid.uuid4(),kind='file',actor_id=actor_id,name=item.name,filename=item.filename,file_data=data,size_bytes=len(data),media_type='application/pdf',enabled=item.enabled)
        set_text(row,text);db.add(row)
    if files:
        db.add(AgentChange(workspace_id=agent.workspace_id,agent_id=agent.id,agent_name=agent.name,actor_id=actor_id,action='knowledge_imported',summary=str(len(files))+' Knowledge PDF source(s) imported.'))
