"""Agent Knowledge source ingestion, processing leases and reply guidance. Frozen DDL."""
from alembic import op
revision='0018'
down_revision='0017'
branch_labels=None
depends_on=None
DDL=(
    "CREATE TABLE agent_knowledge_sources (\n\tagent_id UUID NOT NULL, \n\tid UUID NOT NULL, \n\tkind VARCHAR(10) NOT NULL, \n\tlegacy BOOLEAN NOT NULL, \n\tactor_id UUID, \n\tname VARCHAR(100) NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tstatus VARCHAR(12) NOT NULL, \n\tcontent TEXT NOT NULL, \n\tchunks JSON NOT NULL, \n\tsource_url VARCHAR(2048) NOT NULL, \n\tresolved_url VARCHAR(2048) NOT NULL, \n\tfilename VARCHAR(250) NOT NULL, \n\tmedia_type VARCHAR(100) NOT NULL, \n\tfile_data BYTEA, \n\tsize_bytes INTEGER NOT NULL, \n\ttext_bytes INTEGER NOT NULL, \n\terror_code VARCHAR(60) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE, \n\tlease_token UUID, \n\tPRIMARY KEY (agent_id, id), \n\tCONSTRAINT ck_knowledge_source_state CHECK (kind IN ('snippet','file','url') AND status IN ('queued','processing','ready','failed') AND revision>=1 AND text_bytes>=0 AND size_bytes>=0), \n\tFOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE CASCADE, \n\tFOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL\n)",
    'CREATE INDEX ix_knowledge_source_queue ON agent_knowledge_sources (status, updated_at)',
    'CREATE TABLE agent_knowledge_policies (\n\tagent_id UUID NOT NULL, \n\tmaster_prompt TEXT NOT NULL, \n\tuse_for_replies BOOLEAN NOT NULL, \n\tfallback_message VARCHAR(500) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (agent_id), \n\tCONSTRAINT ck_knowledge_policy_revision CHECK (revision>=1), \n\tFOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE CASCADE\n)',
    "INSERT INTO agent_knowledge_sources(agent_id,id,kind,legacy,actor_id,name,enabled,status,content,chunks,source_url,resolved_url,filename,media_type,size_bytes,text_bytes,error_code,revision,created_at,updated_at)\nSELECT k.agent_id,(item->>'id')::uuid,'snippet',true,NULL,item->>'name',(item->>'enabled')::boolean,'ready',item->>'content',\n(SELECT json_agg(substr(item->>'content',pos,1600)) FROM generate_series(1,length(item->>'content'),1440) pos),'','','','text/plain',0,octet_length(item->>'content'),'',1,k.updated_at,k.updated_at\nFROM agent_knowledge k CROSS JOIN LATERAL json_array_elements(k.sources) item",
)

def upgrade():
    for statement in DDL:op.execute(statement)

def downgrade():
    op.drop_table('agent_knowledge_policies')
    op.drop_table('agent_knowledge_sources')
