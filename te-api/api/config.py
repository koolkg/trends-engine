"""Env-var config for te-api (no framework)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str
    redis_host: str
    redis_port: int
    cache_prefix: str
    cache_ttl_seconds: int
    default_limit: int
    max_limit: int
    mailchimp_api_url: str
    mailchimp_api_key: str
    mailchimp_timeout_seconds: float
    mailchimp_from_name: str
    mailchimp_reply_to: str


def load_config() -> Config:
    env = os.environ.get
    return Config(
        host=env("API_HOST", "0.0.0.0"),
        port=int(env("API_PORT", "8080")),
        postgres_host=env("POSTGRES_HOST", "localhost"),
        postgres_port=int(env("POSTGRES_PORT", "5432")),
        postgres_user=env("POSTGRES_USER", "trends"),
        postgres_password=env("POSTGRES_PASSWORD", "trends_dev_pw"),
        postgres_db=env("POSTGRES_DB", "trends"),
        redis_host=env("REDIS_HOST", "localhost"),
        redis_port=int(env("REDIS_PORT", "6379")),
        cache_prefix=env("API_CACHE_PREFIX", "trends"),  # te-aggregator writes <prefix>:current / <prefix>:v:<version>
        cache_ttl_seconds=int(env("API_CACHE_TTL_SECONDS", "3600")),
        default_limit=int(env("API_DEFAULT_LIMIT", "20")),
        max_limit=int(env("API_MAX_LIMIT", "100")),
        # Mailchimp Marketing API. The PoC points at te-infra's mailchimp-fake;
        # the real one is https://<dc>.api.mailchimp.com/3.0 with a real key.
        mailchimp_api_url=env("MAILCHIMP_API_URL", "http://localhost:8090/3.0"),
        mailchimp_api_key=env("MAILCHIMP_API_KEY", "fake-key-us1"),
        mailchimp_timeout_seconds=float(env("MAILCHIMP_TIMEOUT_SECONDS", "10")),
        mailchimp_from_name=env("MAILCHIMP_FROM_NAME", "Trends Engine"),
        mailchimp_reply_to=env("MAILCHIMP_REPLY_TO", "hello@example.com"),
    )
