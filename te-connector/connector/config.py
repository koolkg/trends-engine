"""Env-var config loader for te-connector — no framework, just os.environ
with defaults. Defaults for the RabbitMQ settings match
te-deployment/.env.example so the two line up without any extra setup.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_pass: str
    exchange: str
    fixture_path: str
    source_id: str
    batch_size: int
    poll_interval_seconds: float
    loop_fixture: bool
    log_messages: bool = True   # log every published message in full (CONNECTOR_LOG_MESSAGES)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_config() -> Config:
    return Config(
        rabbitmq_host=os.environ.get("RABBITMQ_HOST", "localhost"),
        rabbitmq_port=int(os.environ.get("RABBITMQ_PORT", "5672")),
        rabbitmq_user=os.environ.get("RABBITMQ_USER", "trends"),
        rabbitmq_pass=os.environ.get("RABBITMQ_PASS", "trends_dev_pw"),
        exchange=os.environ.get("RABBITMQ_EXCHANGE", "trends.topic"),
        fixture_path=os.environ.get("CONNECTOR_FIXTURE_PATH", "fixtures/raw_content.jsonl"),
        source_id=os.environ.get("CONNECTOR_SOURCE_ID", "src_tiktok_hashtag"),
        batch_size=int(os.environ.get("CONNECTOR_BATCH_SIZE", "5")),
        poll_interval_seconds=float(os.environ.get("CONNECTOR_POLL_INTERVAL_SECONDS", "30")),
        loop_fixture=_bool_env("CONNECTOR_LOOP_FIXTURE", True),
        log_messages=_bool_env("CONNECTOR_LOG_MESSAGES", True),
    )
