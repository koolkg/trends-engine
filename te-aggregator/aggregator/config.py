"""Env-var config for te-aggregator (no framework)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_pass: str
    poll_queue: str
    score_queue: str
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str
    redis_host: str
    redis_port: int
    fallback_seconds: float
    debounce_seconds: float
    max_wait_seconds: float
    top_n: int
    lookback_hours: float
    sparkline_hours: int
    decay_per_hour: float
    min_z: float
    spike_z: float
    cache_ttl_seconds: int


def load_config() -> Config:
    env = os.environ.get
    return Config(
        rabbitmq_host=env("RABBITMQ_HOST", "localhost"),
        rabbitmq_port=int(env("RABBITMQ_PORT", "5672")),
        rabbitmq_user=env("RABBITMQ_USER", "trends"),
        rabbitmq_pass=env("RABBITMQ_PASS", "trends_dev_pw"),
        poll_queue=env("AGGREGATOR_POLL_QUEUE", "q.aggregator.source-poll-completed"),
        score_queue=env("AGGREGATOR_SCORE_QUEUE", "q.aggregator.trend-score-upserted"),
        postgres_host=env("POSTGRES_HOST", "localhost"),
        postgres_port=int(env("POSTGRES_PORT", "5432")),
        postgres_user=env("POSTGRES_USER", "trends"),
        postgres_password=env("POSTGRES_PASSWORD", "trends_dev_pw"),
        postgres_db=env("POSTGRES_DB", "trends"),
        redis_host=env("REDIS_HOST", "localhost"),
        redis_port=int(env("REDIS_PORT", "6379")),
        fallback_seconds=float(env("AGGREGATOR_FALLBACK_SECONDS", "300")),   # design doc: 5-min timer
        debounce_seconds=float(env("AGGREGATOR_DEBOUNCE_SECONDS", "20")),    # ~20 s debounce
        max_wait_seconds=float(env("AGGREGATOR_MAX_WAIT_SECONDS", "90")),    # ~90 s cap
        top_n=int(env("AGGREGATOR_TOP_N", "10")),
        lookback_hours=float(env("AGGREGATOR_LOOKBACK_HOURS", "6")),
        sparkline_hours=int(env("AGGREGATOR_SPARKLINE_HOURS", "12")),
        decay_per_hour=float(env("AGGREGATOR_DECAY_PER_HOUR", "0.8")),
        min_z=float(env("AGGREGATOR_MIN_Z", "1.0")),
        spike_z=float(env("AGGREGATOR_SPIKE_Z", "3.0")),
        cache_ttl_seconds=int(env("AGGREGATOR_CACHE_TTL_SECONDS", "3600")),
    )
