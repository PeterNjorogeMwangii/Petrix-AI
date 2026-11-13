from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    session_signing_secret: SecretStr
    frontend_origin: str = "http://localhost:4200"
    api_base_url: str = "http://localhost:8000"
    magic_link_minutes: int = 15
    session_hours: int = 168
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = False
    smtp_ssl: bool = False
    smtp_from: str = "Petrix AI <hello@petrix.local>"
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    microsoft_client_id: str = ""
    microsoft_client_secret: SecretStr = SecretStr("")
    microsoft_tenant: str = "common"
    blocked_email_domains: str = ""
    supported_countries: str = ""
    default_country: str = "KE"
    email_limit_per_window: int = 5
    ip_limit_per_window: int = 30
    rate_limit_minutes: int = 15
    default_agent_limit: int = 3
    default_language_limit: int = 3
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: int = 30
    llm_max_tokens: int = 1024
    llm_tester_requests_per_minute: int = 20
    intent_semantic_enabled: bool = True
    intent_fuzzy_margin: float = 0.10
    intent_semantic_threshold: float = 0.70
    intent_semantic_margin: float = 0.08
    embedding_provider: Literal["fastembed", "openai_compatible"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = ".cache/embeddings"
    embedding_base_url: str = ""
    embedding_api_key: SecretStr = SecretStr("")
    embedding_timeout_seconds: int = 10
    groq_api_key: SecretStr = SecretStr("")
    groq_intent_model: str = "openai/gpt-oss-20b"
    intent_llm_enabled: bool = True
    intent_llm_threshold: float = 0.80
    intent_llm_timeout_seconds: int = 15
    intent_llm_max_tokens: int = 2048
    llm_processing_max_tokens: int = 4096
    send_api_allowed_private_hosts: str = ""
    database_connections_encryption_key: SecretStr = SecretStr("")

    @field_validator("database_connections_encryption_key")
    @classmethod
    def valid_database_key(cls, value):
        if value.get_secret_value():
            from cryptography.fernet import Fernet
            try: Fernet(value.get_secret_value().encode("ascii"))
            except (ValueError, UnicodeError):
                raise ValueError("DATABASE_CONNECTIONS_ENCRYPTION_KEY must be a Fernet key (32 URL-safe base64-encoded bytes).") from None
        return value

    @field_validator("send_api_allowed_private_hosts")
    @classmethod
    def private_api_hosts(cls, value):
        import re
        hosts = [host.strip().lower().rstrip(".") for host in value.split(",") if host.strip()]
        if any(not re.fullmatch(r"[a-z0-9.:-]+", host) for host in hosts):
            raise ValueError("List exact API hostnames or IP addresses, separated by commas, without schemes, paths, or wildcards.")
        return ",".join(dict.fromkeys(hosts))

    @field_validator("frontend_origin", "api_base_url")
    @classmethod
    def valid_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Use a complete http(s) origin.")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("Origins cannot contain paths, queries, credentials, or fragments.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def valid_runtime(self):
        if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("Petrix AI requires PostgreSQL through the psycopg driver.")
        if len(self.session_signing_secret.get_secret_value()) < 32:
            raise ValueError("SESSION_SIGNING_SECRET must contain at least 32 characters.")
        if not 1 <= self.magic_link_minutes <= 60 or not 1 <= self.session_hours <= 720:
            raise ValueError("Authentication expiry settings are out of range.")
        if min(self.email_limit_per_window, self.ip_limit_per_window, self.rate_limit_minutes) < 1:
            raise ValueError("Rate limits must be positive.")
        if self.default_agent_limit < 1 or not 1 <= self.default_language_limit <= 20:
            raise ValueError("Agent and language limits must be positive; at most 20 languages are supported.")
        if not 1 <= self.llm_timeout_seconds <= 120 or not 1 <= self.llm_max_tokens <= 4096 or self.llm_tester_requests_per_minute < 1:
            raise ValueError("Language model request limits are invalid.")
        scores = (self.intent_fuzzy_margin, self.intent_semantic_margin, self.intent_semantic_threshold, self.intent_llm_threshold)
        if any(not 0 <= value <= 1 for value in scores):
            raise ValueError("Intent similarity thresholds and margins must be between 0 and 1.")
        if not 1 <= self.embedding_timeout_seconds <= 60 or not 1 <= self.intent_llm_timeout_seconds <= 60 or not 256 <= self.intent_llm_max_tokens <= 4096:
            raise ValueError("Intent provider request limits are invalid.")
        if not 256 <= self.llm_processing_max_tokens <= 8192:
            raise ValueError("LLM_PROCESSING_MAX_TOKENS must be between 256 and 8192.")
        if not self.embedding_model.strip() or not self.groq_intent_model.strip():
            raise ValueError("Specify embedding and intent model names.")
        if self.embedding_base_url:
            parsed = urlsplit(self.embedding_base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
                raise ValueError("Use an embedding base URL without credentials, query, or fragment.")
            if self.app_env == "production" and parsed.scheme != "https":
                raise ValueError("Production embedding connections require HTTPS.")
        if self.llm_base_url:
            parsed = urlsplit(self.llm_base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
                raise ValueError("Use a complete language model base URL without credentials, query, or fragment.")
            if self.app_env == "production" and parsed.scheme != "https":
                raise ValueError("Production language model connections require HTTPS.")
        if self.smtp_ssl and self.smtp_starttls:
            raise ValueError("Choose SMTP SSL or STARTTLS, not both.")
        if self.app_env == "production":
            if not self.frontend_origin.startswith("https://") or not self.api_base_url.startswith("https://"):
                raise ValueError("Production origins require HTTPS.")
            if not (self.smtp_ssl or self.smtp_starttls):
                raise ValueError("Production SMTP requires SSL or STARTTLS.")
        return self

    @property
    def secure_cookies(self) -> bool:
        return self.app_env == "production"

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret.get_secret_value())

    @property
    def microsoft_enabled(self) -> bool:
        return bool(self.microsoft_client_id and self.microsoft_client_secret.get_secret_value())
