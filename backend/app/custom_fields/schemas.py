"""Conversation custom fields with explicit, bounded option schemas."""
import math
from datetime import date
from typing import Any, List, Literal, Optional
import json
import uuid

from pydantic import Field, StrictBool, StrictInt, model_validator
from app.agent_schemas import StrictModel

FieldType = Literal['text', 'number', 'toggle', 'dropdown', 'date', 'single_select_grouped']


class Option(StrictModel):
    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=1000)


class Group(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    options: List[Option] = Field(min_length=1, max_length=100)


class FieldCreate(StrictModel):
    scope: Literal['conversation'] = 'conversation'
    type: FieldType
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    options: List[Any] = Field(default_factory=list, max_length=200)
    default_value: Any = None

    @model_validator(mode='after')
    def valid_settings(self):
        if self.type == 'single_select_grouped':
            groups = [Group.model_validate(item) for item in self.options]
            if not groups or len(groups)>20 or len({g.name.casefold() for g in groups}) != len(groups):
                raise ValueError('Add one or more uniquely named option groups.')
            options = [item for group in groups for item in group.options]
            self.options = [g.model_dump(mode='json') for g in groups]
        elif self.type == 'dropdown':
            options = [Option.model_validate(item) for item in self.options]
            if not options: raise ValueError('Add at least one dropdown option.')
            self.options = [item.model_dump(mode='json') for item in options]
        else:
            if self.options: raise ValueError('Only select fields accept Field Options.')
            options = []
        if len(options) > 200 or len({o.value for o in options}) != len(options):
            raise ValueError('Use at most 200 options with unique values per field.')
        if len(json.dumps(self.options, ensure_ascii=False).encode('utf-8')) > 65536:
            raise ValueError('Keep Field Options within 64 KiB.')
        value = self.default_value
        if value is not None:
            if self.type == 'text' and (type(value) is not str or len(value) > 4000):
                raise ValueError('Text defaults support up to 4,000 characters.')
            if self.type == 'number':
                try: valid_number = type(value) in {int, float} and math.isfinite(value)
                except OverflowError: valid_number = False
                if not valid_number: raise ValueError('Use a finite number as the default.')
            if self.type == 'toggle' and type(value) is not bool: raise ValueError('Use true or false as the default.')
            if self.type == 'date':
                if type(value) is not str or len(value) != 10: raise ValueError('Use YYYY-MM-DD as the default date.')
                try: date.fromisoformat(value)
                except ValueError: raise ValueError('Use a valid YYYY-MM-DD date.') from None
            if self.type in {'dropdown','single_select_grouped'} and type(value) is str:
                value = value.strip(); self.default_value = value
            if self.type in {'dropdown','single_select_grouped'} and (type(value) is not str or value not in {o.value for o in options}):
                raise ValueError('Choose an existing option value as the default.')
        return self


class FieldUpdate(FieldCreate):
    expected_revision: StrictInt = Field(ge=1)


class DeleteField(StrictModel):
    id: uuid.UUID
    expected_revision: StrictInt = Field(ge=1)


class DeleteFields(StrictModel):
    fields: List[DeleteField] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_ids(self):
        if len({f.id for f in self.fields}) != len(self.fields): raise ValueError('Select each field once.')
        return self
