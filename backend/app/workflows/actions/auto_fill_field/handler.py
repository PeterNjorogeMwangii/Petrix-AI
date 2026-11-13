from copy import deepcopy
from dataclasses import dataclass
from app.workflows.chat_script import ScriptError
from app.workflows.actions.auto_fill_field.service import FillResult


@dataclass(frozen=True)
class AutoFillOutcome:
    path_id: str
    outcome: str
    error: object
    assigned_count: int
    unmatched_count: int
    model: str


def execute(config,context):
    client=context.auto_fill_client
    model=client.model if client else ''
    def outcome(error=None,assigned=0,unmatched=0):
        status='error' if error else 'success'
        return AutoFillOutcome(config[status+'_path_id'],status,error,assigned,unmatched,model)
    try:context.assign(config['variable_name'],None)
    except ScriptError:return outcome('variable_limit')
    result=client.fill(config['field_ids'],context) if client else FillResult(error='provider_unavailable')
    value={'ok':not bool(result.error),'fields':{key:{name:item[name] for name in ('field_id','value','label','group')}
        for key,item in result.values.items()},'unmatched':result.unmatched,'error':result.error}
    try:context.assign(config['variable_name'],value)
    except ScriptError:return outcome('variable_limit')
    if result.error:return outcome(result.error)
    context.state['custom_fields']={**deepcopy(context.state.get('custom_fields',{})),**deepcopy(result.values)}
    return outcome(assigned=len(result.values),unmatched=len(result.unmatched))
