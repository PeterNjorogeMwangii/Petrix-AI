from typing import Optional

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator


class EmailRequest(BaseModel):
    email: EmailStr


class VerifyRequest(BaseModel):
    token: str = Field(min_length=30, max_length=128)


class ProviderEmailRequest(EmailRequest):
    ticket: str = Field(min_length=30, max_length=128)


class WorkspaceSetup(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    country_code: str = Field(min_length=2, max_length=2)
    industry: str = "Other"
    website: Optional[HttpUrl] = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Workspace name is required.")
        return value

    @field_validator("country_code")
    @classmethod
    def clean_country(cls, value: str) -> str:
        return value.upper()
