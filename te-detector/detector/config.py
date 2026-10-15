"""Env-var config for te-detector (no framework). Defaults suit the PoC's
volume; see README for why the window is an hour, not the design doc's 5 min."""
import os
from dataclasses import dataclass
from typing import FrozenSet

# Platform-boilerplate and catch-all tags: on nearly every post, so they say
# nothing about what's trending. Override with DETECTOR_IGNORE_TAGS (comma list).
DEFAULT_IGNORE_TAGS = (
    "fyp,foryou,foryoupage,viral,trending,marketing,smallbusiness,contentcreator,"
    "socialmediatips,marketingtips,entrepreneur,digitalmarketing,creatoreconomy,"
    "ugc,beauty,makeup,skincare,life,weekend,mood,love,instagood"
)


def parse_tags(raw: str) -> FrozenSet[str]:
    return frozenset(t.strip().lstrip("#").lower() for t in raw.split(",") if t.strip())


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
    window_minutes: int
    ewma_alpha: float
    z_threshold: float
    min_count: int
    warmup_windows: int
    idle_flush_seconds: float
    ignore_tags: FrozenSet[str]


def load_config() -> Config:
    env = os.environ.get
    return Config(
        rabbitmq_host=env("RABBITMQ_HOST", "localhost"),
        rabbitmq_port=int(env("RABBITMQ_PORT", "5672")),
        rabbitmq_user=env("RABBITMQ_USER", "trends"),
        rabbitmq_pass=env("RABBITMQ_PASS", "trends_dev_pw"),
        exchange=env("RABBITMQ_EXCHANGE", "trends.topic"),
        queue=env("DETECTOR_QUEUE", "q.detector.content-normalized"),
        prefetch=int(env("DETECTOR_PREFETCH", "100")),
        postgres_host=env("POSTGRES_HOST", "localhost"),
        postgres_port=int(env("POSTGRES_PORT", "5432")),
        postgres_user=env("POSTGRES_USER", "trends"),
        postgres_password=env("POSTGRES_PASSWORD", "trends_dev_pw"),
        postgres_db=env("POSTGRES_DB", "trends"),
        window_minutes=int(env("DETECTOR_WINDOW_MINUTES", "60")),
        ewma_alpha=float(env("DETECTOR_EWMA_ALPHA", "0.3")),
        z_threshold=float(env("DETECTOR_Z_THRESHOLD", "3.0")),
        min_count=int(env("DETECTOR_MIN_COUNT", "4")),
        warmup_windows=int(env("DETECTOR_WARMUP_WINDOWS", "2")),
        idle_flush_seconds=float(env("DETECTOR_IDLE_FLUSH_SECONDS", "120")),
        ignore_tags=parse_tags(env("DETECTOR_IGNORE_TAGS", DEFAULT_IGNORE_TAGS)),
    )
