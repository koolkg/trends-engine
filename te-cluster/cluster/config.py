"""Env-var config for te-cluster (no framework)."""
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
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_db: str
    model_name: str
    batch_size: int
    batch_wait_ms: int
    similarity_threshold: float
    evict_after_hours: float
    window_minutes: int
    ewma_alpha: float
    z_threshold: float
    min_count: int
    warmup_windows: int
    idle_flush_seconds: float


def load_config() -> Config:
    env = os.environ.get
    return Config(
        rabbitmq_host=env("RABBITMQ_HOST", "localhost"),
        rabbitmq_port=int(env("RABBITMQ_PORT", "5672")),
        rabbitmq_user=env("RABBITMQ_USER", "trends"),
        rabbitmq_pass=env("RABBITMQ_PASS", "trends_dev_pw"),
        exchange=env("RABBITMQ_EXCHANGE", "trends.topic"),
        queue=env("CLUSTER_QUEUE", "q.cluster.content-normalized"),
        postgres_host=env("POSTGRES_HOST", "localhost"),
        postgres_port=int(env("POSTGRES_PORT", "5432")),
        postgres_user=env("POSTGRES_USER", "trends"),
        postgres_password=env("POSTGRES_PASSWORD", "trends_dev_pw"),
        postgres_db=env("POSTGRES_DB", "trends"),
        model_name=env("CLUSTER_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        batch_size=int(env("CLUSTER_BATCH_SIZE", "64")),          # design doc: up to 64 messages...
        batch_wait_ms=int(env("CLUSTER_BATCH_WAIT_MS", "50")),     # ...or a 50ms flush window
        similarity_threshold=float(env("CLUSTER_SIMILARITY_THRESHOLD", "0.72")),  # design doc's starting point
        evict_after_hours=float(env("CLUSTER_EVICT_AFTER_HOURS", "24")),  # design doc: cold eviction after 24h
        window_minutes=int(env("CLUSTER_WINDOW_MINUTES", "60")),  # same windows as te-detector, so z-scores compare
        ewma_alpha=float(env("CLUSTER_EWMA_ALPHA", "0.3")),
        z_threshold=float(env("CLUSTER_Z_THRESHOLD", "3.0")),
        min_count=int(env("CLUSTER_MIN_COUNT", "4")),
        warmup_windows=int(env("CLUSTER_WARMUP_WINDOWS", "2")),
        idle_flush_seconds=float(env("CLUSTER_IDLE_FLUSH_SECONDS", "120")),
    )
