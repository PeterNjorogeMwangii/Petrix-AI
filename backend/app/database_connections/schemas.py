import ipaddress
import re
from typing import Literal, Optional

from pydantic import Field, SecretStr, StrictInt, field_validator

from app.agent_schemas import StrictModel


class ConnectionFields(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    type: Literal["postgresql"] = "postgresql"
    host: str = Field(min_length=1, max_length=253)
    port: StrictInt = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    ssl_mode: Literal["verify-full", "require", "disable"] = "verify-full"

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value) or any(
                not part or len(part) > 63 or part.startswith("-") or part.endswith("-") for part in value.split(".")
            ):
                raise ValueError("Enter one database hostname or IP address, without a URL, path, or port.") from None
            return value.lower()

    @field_validator("database", "username", "name")
    @classmethod
    def printable(cls, value):
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Use text without control characters.")
        return value


class ConnectionCreate(ConnectionFields):
    password: SecretStr = Field(min_length=1, max_length=1024)


class ConnectionUpdate(ConnectionFields):
    expected_revision: StrictInt = Field(ge=1)
    password: Optional[SecretStr] = Field(default=None, max_length=1024)


class ConnectionRevision(StrictModel):
    expected_revision: StrictInt = Field(ge=1)
