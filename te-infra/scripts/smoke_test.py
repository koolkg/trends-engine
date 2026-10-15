#!/usr/bin/env python3
"""
Phase 0 integration smoke test for te-infra.

Confirms:
  1. Postgres and Redis are reachable, and Postgres has the schema from
     postgres/init/001_schema.sql (the "table-count check").
  2. One message round-trips through each of the four topics declared in
     rabbitmq/definitions.json, including the two topics that fan out to
     two queues (content.raw.ingested and content.normalized each have
     one consumer; content.normalized fans out to both te-detector and
     te-cluster).

Run via `make test-integration` (brings te-infra up first) or directly,
once te-infra is already up, with `python3 scripts/smoke_test.py`.
"""
import os
import sys
import time
import uuid

import pika
import psycopg2
import redis

ENV = {
    "pg_host": os.environ.get("POSTGRES_HOST", "localhost"),
    "pg_port": int(os.environ.get("POSTGRES_PORT", 5432)),
    "pg_user": os.environ.get("POSTGRES_USER", "trends"),
    "pg_password": os.environ.get("POSTGRES_PASSWORD", "trends_dev_pw"),
    "pg_db": os.environ.get("POSTGRES_DB", "trends"),
    "redis_host": os.environ.get("REDIS_HOST", "localhost"),
    "redis_port": int(os.environ.get("REDIS_PORT", 6379)),
    "rabbit_host": os.environ.get("RABBITMQ_HOST", "localhost"),
    "rabbit_port": int(os.environ.get("RABBITMQ_PORT", 5672)),
    "rabbit_user": os.environ.get("RABBITMQ_USER", "trends"),
    "rabbit_pass": os.environ.get("RABBITMQ_PASS", "trends_dev_pw"),
}

EXPECTED_TABLES = {
    "source", "raw_content_item", "normalized_content_item",
    "term_frequency_snapshot", "content_cluster_assignment",
    "cluster_centroid", "trend_ranking", "campaign_draft_block",
    "cluster_velocity_snapshot",  # 002_cluster_velocity.sql
}

# (routing_key, [queues bound to it]) — mirrors rabbitmq/definitions.json
TOPICS = [
    ("content.raw.ingested", ["q.normalizer.content-raw-ingested"]),
    ("content.normalized", [
        "q.detector.content-normalized",
        "q.cluster.content-normalized",
    ]),
    ("trend.score.upserted", ["q.aggregator.trend-score-upserted"]),
    ("source.poll.completed", ["q.aggregator.source-poll-completed"]),
    ("cluster.score.upserted", ["q.aggregator.trend-score-upserted"]),  # te-cluster
]


def check_postgres():
    print("[postgres] connecting...")
    conn = psycopg2.connect(
        host=ENV["pg_host"], port=ENV["pg_port"], user=ENV["pg_user"],
        password=ENV["pg_password"], dbname=ENV["pg_db"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            found = {row[0] for row in cur.fetchall()}
        missing = EXPECTED_TABLES - found
        if missing:
            raise AssertionError(f"missing tables: {sorted(missing)}")
        print(f"[postgres] ok — {len(EXPECTED_TABLES)} expected tables present")
    finally:
        conn.close()


def check_redis():
    print("[redis] connecting...")
    r = redis.Redis(host=ENV["redis_host"], port=ENV["redis_port"])
    key = f"smoke:{uuid.uuid4()}"
    r.set(key, "ok", ex=30)
    assert r.get(key) == b"ok", "set/get round-trip failed"
    r.delete(key)
    print("[redis] ok — set/get round-trip")


def check_rabbitmq_roundtrip():
    print("[rabbitmq] connecting...")
    creds = pika.PlainCredentials(ENV["rabbit_user"], ENV["rabbit_pass"])
    params = pika.ConnectionParameters(
        host=ENV["rabbit_host"], port=ENV["rabbit_port"], credentials=creds,
    )
    conn = pika.BlockingConnection(params)
    try:
        channel = conn.channel()
        for routing_key, queues in TOPICS:
            marker = str(uuid.uuid4())
            channel.basic_publish(
                exchange="trends.topic",
                routing_key=routing_key,
                body=marker.encode(),
            )
            for q in queues:
                deadline = time.time() + 5
                received = None
                while time.time() < deadline:
                    method, _, body = channel.basic_get(queue=q, auto_ack=True)
                    if method is not None:
                        received = body.decode()
                        break
                    time.sleep(0.1)
                if received != marker:
                    raise AssertionError(
                        f"{routing_key!r} -> {q!r}: expected {marker!r}, "
                        f"got {received!r}"
                    )
            print(f"[rabbitmq] ok — {routing_key} -> {queues}")
    finally:
        conn.close()


def check_mailchimp_fake():
    import base64
    import json
    import urllib.request
    url = os.environ.get("MAILCHIMP_API_URL", "http://localhost:8090/3.0")
    print(f"[mailchimp-fake] {url}/ping ...")
    req = urllib.request.Request(url + "/ping")
    key = os.environ.get("MAILCHIMP_API_KEY", "fake-key-us1")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"smoke:{key}".encode()).decode())
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.loads(r.read()).get("health_status"), "no health_status in /ping"
    print("[mailchimp-fake] ok — /ping answered")


def main():
    checks = [check_postgres, check_redis, check_rabbitmq_roundtrip, check_mailchimp_fake]
    failures = []
    for check in checks:
        try:
            check()
        except Exception as exc:  # smoke test: report and keep going
            failures.append(f"{check.__name__}: {exc}")
            print(f"[FAIL] {check.__name__}: {exc}")
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        sys.exit(1)
    print("\nall te-infra smoke checks passed")


if __name__ == "__main__":
    main()
