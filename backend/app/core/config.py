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
    advisor_base_url: str = ""
    advisor_api_key: SecretStr = SecretStr("")
    advisor_model: str = ""
    advisor_vision_enabled: bool = False
    advisor_worker_enabled: bool = True
    webchat_worker_enabled: bool = True
    whatsapp_worker_enabled: bool = True
    whatsapp_graph_version: str = 'v27.0'
    support_ai_base_url: str = ""
    support_ai_api_key: SecretStr = SecretStr("")
    support_ai_model: str = ""
    support_ai_models: str = ""
    perception_provider_url: str = ""
    perception_provider_token: SecretStr = SecretStr("")
    perception_worker_enabled: bool = True
    support_worker_enabled: bool = True
    support_worker_interval: int = 5
    stripe_secret_key: SecretStr = SecretStr("")
    stripe_webhook_secret: SecretStr = SecretStr("")
    stripe_products_json: str = "{}"
    usage_weights_json: str = "{}"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = False
    smtp_ssl: bool = False
    ticket_mail_webhook_secret: SecretStr = SecretStr('')
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
    knowledge_egress_ip: str = ""
    knowledge_worker_enabled: bool = True
    moderation_base_url: str = ""
    moderation_api_key: SecretStr = SecretStr("")
    moderation_model: str = ""
    moderation_required: bool = False

    database_connections_encryption_key: SecretStr = SecretStr("")

    @field_validator('whatsapp_graph_version')
    @classmethod
    def graph_version(cls,value):
        import re
        if not re.fullmatch(r'v[1-9][0-9]\.0',value):raise ValueError('Use a pinned Meta Graph API version, such as v27.0.')
        return value

    @field_validator("perception_provider_url")
    @classmethod
    def perception_url(cls,value):
        if value:
            parsed=urlsplit(value)
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Use a complete collection endpoint URL without credentials, query or fragment.')
            if parsed.scheme=='http' and parsed.hostname not in {'localhost','127.0.0.1','::1'}:
                raise ValueError('Collection endpoints require HTTPS, except local loopback development.')
        return value.rstrip('/')

    @field_validator("support_ai_base_url", "moderation_base_url", "advisor_base_url")
    @classmethod
    def support_url(cls,value):
        if value:
            parsed=urlsplit(value)
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Use a complete HTTP(S) AI API base URL without credentials, query or fragment.')
        return value.rstrip('/')

    @field_validator("support_worker_interval")
    @classmethod
    def worker_interval(cls,value):
        if not 1<=value<=300:raise ValueError('Support worker interval must be 1–300 seconds.')
        return value

    @field_validator("stripe_products_json", "usage_weights_json")
    @classmethod
    def billing_json(cls,value,info):
        import json, math
        try:
            data=json.loads(value)
            if not isinstance(data,dict):raise ValueError()
            if info.field_name=='usage_weights_json':
                if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=100000 for v in data.values()):raise ValueError()
            else:
                import re
                for key,item in data.items():
                    if not isinstance(item,dict) or not re.fullmatch(r'price_[a-zA-Z0-9_]+',item.get('price','')) or item.get('mode') not in {'payment','subscription'}:raise ValueError()
                    if item['mode']=='payment' and (not isinstance(item.get('credits'),int) or item['credits']<=0):raise ValueError()
                    if item.get('included') is not None and (not isinstance(item['included'],int) or item['included']<0):raise ValueError()
                    if any(v is not None and (not isinstance(v,int) or v<1) for v in item.get('limits',{}).values()):raise ValueError()
            return value
        except (ValueError,TypeError,AttributeError):raise ValueError('Use a valid billing product or usage-weight JSON object.') from None

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
        if not 1 <= self.default_language_limit <= 20:
            raise ValueError("The default language limit must be from 1 to 20.")
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
        if self.app_env == 'production' and self.advisor_base_url and not self.advisor_base_url.startswith('https://'):
            raise ValueError('Production Advisor connections require HTTPS.')
        if self.app_env == 'production' and self.moderation_base_url and not self.moderation_base_url.startswith('https://'):
            raise ValueError('Production moderation connections require HTTPS.')
        if self.knowledge_egress_ip:
            import ipaddress
            ipaddress.ip_address(self.knowledge_egress_ip)
        if self.ticket_mail_webhook_secret.get_secret_value() and len(self.ticket_mail_webhook_secret.get_secret_value()) < 32:
            raise ValueError('TICKET_MAIL_WEBHOOK_SECRET must contain at least 32 characters.')
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
