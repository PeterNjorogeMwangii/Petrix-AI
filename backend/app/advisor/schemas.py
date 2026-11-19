import uuid
from typing import List
from pydantic import Field,model_validator
from app.agent_schemas import StrictModel

class Create(StrictModel):
    client_id:uuid.UUID
    teamspace_id:uuid.UUID
class Send(StrictModel):
    client_request_id:uuid.UUID
    expected_revision:int=Field(ge=1)
    content:str=Field('',max_length=8000)
    attachment_ids:List[uuid.UUID]=Field(default_factory=list,max_length=5)
    @model_validator(mode='after')
    def message(self):
        if not self.content.strip() and not self.attachment_ids:raise ValueError('Ask a question or attach a file.')
        if len(set(self.attachment_ids))!=len(self.attachment_ids):raise ValueError('Choose each attachment once.')
        self.content=self.content.strip();return self
class Revision(StrictModel):
    expected_revision:int=Field(ge=1)
class Delete(Revision):
    confirmed:bool=False
