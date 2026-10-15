"""Env-var config for te-normalizer — no framework, just os.environ with
defaults. Host defaults (localhost) suit running outside a container; the
compose file overrides them with te-infra's service names."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_pass: str
    exchange: str
    queue: str
    prefetch: int
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str
    relevance_threshold: float
    classifier: str = "model"       # "model" (model + keyword backup/validator) or "keywords"
    model_sure: float = 0.40
    model_floor: float = 0.25
    model_margin: float = 0.05
    keyword_override: float = 1.0


def load_config() -> Config:
    env = os.environ.get
    return Config(
        rabbitmq_host=env("RABBITMQ_HOST", "localhost"),
        rabbitmq_port=int(env("RABBITMQ_PORT", "5672")),
        rabbitmq_user=env("RABBITMQ_USER", "trends"),
        rabbitmq_pass=env("RABBITMQ_PASS", "trends_dev_pw"),
        exchange=env("RABBITMQ_EXCHANGE", "trends.topic"),
        queue=env("NORMALIZER_QUEUE", "q.normalizer.content-raw-ingested"),
        prefetch=int(env("NORMALIZER_PREFETCH", "50")),
        postgres_host=env("POSTGRES_HOST", "localhost"),
        postgres_port=int(env("POSTGRES_PORT", "5432")),
        postgres_user=env("POSTGRES_USER", "trends"),
        postgres_password=env("POSTGRES_PASSWORD", "trends_dev_pw"),
        postgres_db=env("POSTGRES_DB", "trends"),
        relevance_threshold=float(env("NORMALIZER_RELEVANCE_THRESHOLD", "0.5")),
        classifier=env("NORMALIZER_CLASSIFIER", "model"),
        model_sure=float(env("NORMALIZER_MODEL_SURE", "0.40")),
        model_floor=float(env("NORMALIZER_MODEL_FLOOR", "0.25")),
        model_margin=float(env("NORMALIZER_MODEL_MARGIN", "0.05")),
        keyword_override=float(env("NORMALIZER_KEYWORD_OVERRIDE", "1.0")),
    )
