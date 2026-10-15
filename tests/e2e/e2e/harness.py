"""Shared plumbing for the e2e scenarios: config, clients, waiting helpers,
and the one-time "replay the fixture through the whole stack" step.

The suite plays te-connector's role: it publishes te-connector's fixture
straight to content.raw.ingested at full speed (te-connector itself replays at
5 posts / 30 s, and its own tests cover it). Everything after that is the
real stack: te-normalizer -> te-detector + te-cluster -> te-aggregator -> te-api.
"""
import json
import os
import time
from datetime import datetime
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

import pika
import psycopg2

from e2e.redis_client import Redis

env = os.environ.get
API_URL = env("API_URL", "http://te-api:8080")
MAILCHIMP_API_URL = env("MAILCHIMP_API_URL", "http://mailchimp-fake:8090/3.0")  # te-infra's fake Mailchimp API
MAILCHIMP_API_KEY = env("MAILCHIMP_API_KEY", "fake-key-us1")
FIXTURE = env("E2E_FIXTURE", "/fixtures/raw_content.jsonl")
LABELS = env("E2E_LABELS", "/fixtures/raw_content.labels.jsonl")
PIPELINE_TIMEOUT = float(env("E2E_PIPELINE_TIMEOUT_SECONDS", "600"))  # first run: model warm-up + 1.2k embeddings
CYCLE_TIMEOUT = float(env("E2E_CYCLE_TIMEOUT_SECONDS", "150"))        # debounce 20 s, capped at 90 s
EXCHANGE = "trends.topic"
CURRENT_KEY = "trends:current"
PIPELINE_QUEUES = ("q.normalizer.content-raw-ingested", "q.detector.content-normalized",
                   "q.cluster.content-normalized")
TREND_TAGS = {"#lowercase", "#brandvoice", "#productdemo", "#pov", "#unboxing", "#dupe", "#dupes", "#dupealert",
              "#budgetbeauty", "#affordablemakeup", "#emailmarketing", "#subjectlines", "#emailmarketingtips",
              "#quietluxury", "#oldmoney", "#minimalist", "#founderstory"}
PLATFORMS = {"reddit", "x", "instagram", "tiktok"}


def log(msg: str) -> None:
    print(f"[e2e {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_for(what: str, check: Callable[[], object], timeout: float, interval: float = 2.0):
    """Polls check() until it returns something truthy; fails with `what` on timeout."""
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        try:
            result = check()
            if result:
                return result
        except Exception as exc:  # service still starting: keep waiting
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}" + (f" (last error: {last})" if last else ""))


# -- clients ---------------------------------------------------------------
def pg():
    return psycopg2.connect(host=env("POSTGRES_HOST", "postgres"), port=int(env("POSTGRES_PORT", "5432")),
                            user=env("POSTGRES_USER", "trends"), password=env("POSTGRES_PASSWORD", "trends_dev_pw"),
                            dbname=env("POSTGRES_DB", "trends"))


def query(sql: str, args=()) -> List[tuple]:
    conn = pg()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def redis() -> Redis:
    return Redis(env("REDIS_HOST", "redis"), int(env("REDIS_PORT", "6379")))


def rabbit() -> pika.BlockingConnection:
    return pika.BlockingConnection(pika.ConnectionParameters(
        host=env("RABBITMQ_HOST", "rabbitmq"), port=int(env("RABBITMQ_PORT", "5672")),
        credentials=pika.PlainCredentials(env("RABBITMQ_USER", "trends"), env("RABBITMQ_PASS", "trends_dev_pw"))))


def http(method: str, url: str, body: Optional[Dict] = None, timeout: float = 15) -> Tuple[int, Dict, Dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"null")


def api(path: str) -> Tuple[int, Dict, Dict]:
    return http("GET", API_URL + path)


def mailchimp(path: str) -> Dict:
    """GET from the fake Mailchimp API, to see what te-api put there."""
    import base64
    req = urllib.request.Request(MAILCHIMP_API_URL + path)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"e2e:{MAILCHIMP_API_KEY}".encode()).decode())
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=3)
        return True
    except urllib.error.HTTPError:
        return True   # it answered, just not 2xx
    except Exception:
        return False


# -- fixture ---------------------------------------------------------------
def load_fixture() -> Tuple[List[Dict], List[Dict]]:
    with open(FIXTURE, encoding="utf-8") as f:
        posts = [json.loads(line) for line in f]
    with open(LABELS, encoding="utf-8") as f:
        labels = [json.loads(line) for line in f]
    assert [p["contentId"] for p in posts] == [l["contentId"] for l in labels], "fixture and labels out of sync"
    return posts, labels


# -- pipeline steps ----------------------------------------------------------
def publish(routing_key: str, messages: List[Dict]) -> None:
    conn = rabbit()
    try:
        ch = conn.channel()
        for m in messages:
            ch.basic_publish(exchange=EXCHANGE, routing_key=routing_key, body=json.dumps(m).encode(),
                             properties=pika.BasicProperties(content_type="application/json", delivery_mode=2))
    finally:
        conn.close()


def poll_completed(n: int = 1) -> None:
    publish("source.poll.completed", [{"sourceId": "src_e2e", "pollCycleId": f"pc_e2e_{time.time_ns()}_{i}",
                                       "itemCount": 0, "completedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                                      for i in range(n)])


def queue_depths() -> Dict[str, int]:
    conn = rabbit()
    try:
        ch = conn.channel()
        return {q: ch.queue_declare(queue=q, passive=True).method.message_count for q in PIPELINE_QUEUES}
    finally:
        conn.close()


def current_version() -> Optional[str]:
    r = redis()
    try:
        v = r.get(CURRENT_KEY)
        return v.decode() if v else None
    finally:
        r.close()


def diagnose() -> str:
    """Why might te-aggregator not be publishing? Queue consumers/backlog, score
    counts, the current version and the lock, gathered without failing."""
    lines = []
    try:
        conn = rabbit()
        ch = conn.channel()
        for q in ("q.aggregator.source-poll-completed", "q.aggregator.trend-score-upserted"):
            ok = ch.queue_declare(queue=q, passive=True).method
            lines.append(f"{q}: {ok.consumer_count} consumer(s), {ok.message_count} waiting")
        conn.close()
    except Exception as exc:
        lines.append(f"rabbitmq: {exc}")
    try:
        t, c, ranked = query("SELECT (SELECT count(*) FROM term_frequency_snapshot),"
                             " (SELECT count(*) FROM cluster_velocity_snapshot),"
                             " (SELECT count(*) FROM trend_ranking WHERE rank IS NOT NULL)")[0]
        lines.append(f"postgres: {t} hashtag score windows, {c} cluster score windows, {ranked} trends ranked")
        for table, label in (("term_frequency_snapshot", "hashtag"), ("cluster_velocity_snapshot", "cluster")):
            lo, hi, zmax, rising = query(f"SELECT min(window_end), max(window_end), max(z_score),"
                                         f" count(*) FILTER (WHERE z_score >= 1) FROM {table}")[0]
            lines.append(f"  {label} windows end {lo} .. {hi}; max z {zmax}; {rising} with z >= 1")
    except Exception as exc:  # e.g. a migration not applied
        lines.append(f"postgres: {exc}".strip())
    try:
        r = redis()
        cur, lock = r.get(CURRENT_KEY), r.get("trends:aggregator:lock")
        r.close()
        lines.append(f"redis: trends:current={cur.decode() if cur else None}, aggregator lock {'HELD' if lock else 'free'}")
    except Exception as exc:
        lines.append(f"redis: {exc}")
    return "\n  ".join(lines)


def wait_for_new_cycle(previous: Optional[str], timeout: float = None) -> str:
    timeout = timeout or CYCLE_TIMEOUT
    started, next_note = time.time(), time.time() + 15
    while time.time() - started < timeout:
        v = current_version()
        if v and v != previous:
            return v
        if time.time() >= next_note:
            log(f"still waiting for te-aggregator ({time.time() - started:.0f}s of {timeout:.0f}s)")
            next_note += 15
        time.sleep(1.0)
    report = diagnose()
    hints = []
    if "0 consumer(s)" in report:
        hints.append("nothing is consuming te-aggregator's queues: it isn't running or keeps crashing; see "
                     "`make -C te-deployment logs-te-aggregator`")
    if " 0 hashtag score windows, 0 cluster score windows" in report:
        hints.append("te-detector and te-cluster haven't written any scores, so there's nothing to rank")
    if "does not exist" in report:
        hints.append("a schema migration is missing: restart the stack (`make -C te-deployment up`; postgres-migrate applies it)")
    if "lock HELD" in report:
        hints.append("the aggregation lock is held: another cycle is running or one died holding it (expires in 2 min)")
    raise AssertionError(f"te-aggregator published no new ranking within {timeout:.0f}s.\n  {report}"
                         + "".join(f"\n  hint: {h}" for h in hints))


_REPLAYED = None
_REPLAY_ERROR = None


def replay_fixture_once() -> Dict:
    """Publishes the whole fixture, waits until every stage has processed it,
    then forces one aggregation cycle. Runs once per test run; later callers
    get the cached result. Safe on a stack that has already seen the fixture
    (dedup makes the re-publish a no-op downstream)."""
    global _REPLAYED, _REPLAY_ERROR
    if _REPLAY_ERROR is not None:  # failed once already: don't replay and wait again for every test class
        raise AssertionError("fixture replay failed earlier in this run (see the first error)")
    if _REPLAYED is not None:
        return _REPLAYED
    try:
        _REPLAYED = _replay_fixture()
    except Exception as exc:
        _REPLAY_ERROR = exc
        raise
    return _REPLAYED


def check_no_newer_data(posts: List[Dict]) -> None:
    """Fails fast if Postgres holds posts dated after the fixture's last post,
    e.g. after replaying te-connector's raw_content_v2.jsonl (Mon-Fri) on this
    stack. The detector's clock and the aggregator's 'now' have then moved past
    the fixture, so its posts would be late and never ranked: every scenario
    would fail for a reason that isn't a bug. Doesn't delete anything."""
    fixture_last = max(datetime.fromisoformat(p["postedAt"].replace("Z", "+00:00")) for p in posts)
    newest, newer = query("SELECT max(posted_at), count(*) FILTER (WHERE posted_at > %s) FROM raw_content_item",
                          (fixture_last,))[0]
    if newer:
        raise AssertionError(
            f"newer data: Postgres has {newer} posts dated after the fixture's last post"
            f" ({fixture_last:%Y-%m-%d %H:%M} UTC; newest is {newest:%Y-%m-%d %H:%M} UTC), e.g. from the v2"
            " fixture. The fixture's posts would arrive late and never be ranked.\n"
            "  Start from clean data:  make -C te-deployment reset && make -C te-deployment e2e")


def _replay_fixture() -> Dict:
    posts, labels = load_fixture()
    ids = [p["contentId"] for p in posts]
    wait_for("te-api /healthz", lambda: api("/healthz")[0] == 200, 120)
    check_no_newer_data(posts)

    seen, scored = query("SELECT (SELECT count(DISTINCT external_content_id) FROM raw_content_item"
                         "  WHERE external_content_id = ANY(%s)),"
                         " (SELECT count(*) FROM term_frequency_snapshot)"
                         "  + (SELECT count(*) FROM cluster_velocity_snapshot)", (ids,))[0]
    if seen == len(ids) and scored == 0:
        # Dedup is permanent: re-publishing is skipped by te-normalizer, so nothing would ever be scored.
        raise AssertionError(
            "stale data: Postgres already has every fixture post but no hashtag or cluster scores, so"
            " replaying the fixture can't produce any (te-normalizer's dedup skips every post).\n"
            "  Start from clean data:  make -C te-deployment reset && make -C te-deployment e2e")
    log(f"{seen} of {len(ids)} fixture posts already archived (dedup skips those)")
    log(f"publishing {len(posts)} fixture posts to content.raw.ingested")
    publish("content.raw.ingested", posts)

    def normalized_all():
        n = query("SELECT count(DISTINCT external_content_id) FROM raw_content_item"
                  " WHERE external_content_id = ANY(%s)", (ids,))[0][0]
        return n == len(ids)
    wait_for("te-normalizer to archive every fixture post", normalized_all, PIPELINE_TIMEOUT)
    log("te-normalizer: every post archived")

    def clustered_all():
        missing = query("SELECT count(*) FROM normalized_content_item n"
                        " JOIN raw_content_item r ON r.id = n.raw_content_id AND r.ingested_at = n.raw_content_ingested_at"
                        " WHERE r.external_content_id = ANY(%s)"
                        " AND NOT EXISTS (SELECT 1 FROM content_cluster_assignment a WHERE a.content_id = n.id)",
                        (ids,))[0][0]
        return missing == 0 and all(d == 0 for d in queue_depths().values())
    wait_for("te-cluster to assign every relevant post and all pipeline queues to drain", clustered_all,
             PIPELINE_TIMEOUT, interval=3.0)
    log(f"pipeline drained: {queue_depths()}")

    log("forcing an aggregation cycle (source.poll.completed); state before:\n  " + diagnose())
    before = current_version()
    poll_completed()
    version = wait_for_new_cycle(before)
    log(f"te-aggregator published ranking version {version}")
    ranked = query("SELECT count(*) FROM trend_ranking WHERE rank IS NOT NULL")[0][0]
    if ranked == 0:
        # Every scenario that reads the feed would fail one by one; say why once instead.
        raise AssertionError(
            "te-aggregator published an EMPTY ranking after the replay.\n  " + diagnose() +
            "\n  The aggregator's 'now' is the newest score window end, and it ranks the 6 h before it."
            "\n  Its own view: make -C te-deployment logs-te-aggregator  (the 'cycle (...)' lines show"
            " candidate counts and 'data now').")
    log(f"{ranked} trends ranked")
    return {"posts": posts, "labels": labels, "version": version}
