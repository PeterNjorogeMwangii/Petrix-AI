import uuid
from typing import Any,List,Literal,Optional
from pydantic import Field,model_validator
from app.agent_schemas import StrictModel


class FieldOverride(StrictModel):
    field_id: uuid.UUID
    value: Any


class CreateTicketConfig(StrictModel):
    tag_ids: List[uuid.UUID] = Field(default_factory=list,max_length=50)
    tag_group_ids: List[uuid.UUID] = Field(default_factory=list,max_length=20)
    assignee_type: Literal['none','user','team'] = 'none'
    assignee_id: Optional[uuid.UUID] = None
    email_domain_id: Optional[uuid.UUID] = None
    subject: str = Field(min_length=1,max_length=200)
    description: str = Field(default='',max_length=16000)
    full_name: str = Field(min_length=1,max_length=200)
    email: str = Field(min_length=1,max_length=320)
    phone: str = Field(default='',max_length=50)
    customer_template_id: Optional[uuid.UUID] = None
    assignee_template_id: Optional[uuid.UUID] = None
    custom_fields: List[FieldOverride] = Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def valid_settings(self):
        if (self.assignee_type=='none')!=(self.assignee_id is None):raise ValueError('Select a valid assignee, or choose Unassigned.')
        if (self.customer_template_id or self.assignee_template_id) and not self.email_domain_id:raise ValueError('Choose a verified email sender for notifications.')
        if self.assignee_template_id and self.assignee_type=='none':raise ValueError('Select an assignee before enabling its email notification.')
        if len(set(self.tag_ids))!=len(self.tag_ids) or len(set(self.tag_group_ids))!=len(self.tag_group_ids):raise ValueError('Select each tag or group once.')
        if len({item.field_id for item in self.custom_fields})!=len(self.custom_fields):raise ValueError('Select each custom field once.')
        for value in (self.subject,self.full_name,self.email,self.phone):
            if any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('Single-line ticket fields cannot contain control characters.')
        return self
