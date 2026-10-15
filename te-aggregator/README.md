# te-aggregator

The one place the two trend signals meet. It
takes te-detector's spiking **hashtags** and te-cluster's fast-growing
**clusters**, merges the ones that are the same trend, ranks them, names
and summarizes the top ones, and publishes the ranked feed to Postgres
(`trend_ranking`) and Redis (for te-api).

```
trigger (timer / debounced poll-completed / spike) ─▶ single-flight lock
  ─▶ recent scores (term_frequency_snapshot + cluster_velocity_snapshot)
  ─▶ candidates ─▶ merge same-trend signals ─▶ top 10 ─▶ name + summary + detail
  ─▶ trend_ranking (one transaction) ─▶ Redis trends:v:<version> + trends:current
```

## Quick start

```
make start / make stop / make logs
make rank               # run one cycle now and print the ranking
make test-unit          # no infra
make test-integration   # real Postgres + Redis + RabbitMQ
```

The container is defined in `../te-deployment/docker-compose.yml`; its migration (003) is applied on every start.

## When it runs (AGG-1, the design doc's trigger contract)

- **Timer:** at least every 5 minutes, so the feed never goes stale. Also once at startup.
- **Poll completed:** each `source.poll.completed` restarts a 20 s debounce,
  capped at 90 s from the first notice. A burst of connectors finishing
  together becomes one run, and a steady trickle can't postpone it forever.
- **Spike:** a `trend.score.upserted` or `cluster.score.upserted` with
  `velocityLabel: "spike"` runs a cycle immediately, skipping the debounce.
- Pending notices collapse into one "run owed" flag. A Redis lock
  (`SET NX PX` + compare-and-delete) makes sure only one cycle runs at a time,
  even with several instances.

## How it ranks (AGG-2/3)

- **"Now" is the newest scored window in the data,** not the wall clock, so
  a replayed day ranks the same as a live one.
- **Score:** each signal's best z-score in the last 6 h, discounted by age
  (`z × 0.8^hours_ago`). A signal that never got above z = 1 isn't a
  candidate, so big-but-steady things never rank.
- **Merging:** a cluster joins the hashtag that appears in ≥ 30 % of its
  posts (one trend often splits into several clusters). Then trends whose
  posts mostly overlap merge (e.g. `#lowercase` and `#brandvoice` tag the same posts).

## Naming and detail (AGG-4, decision AGG-8: template, no LLM)

- **Name:** the trend's hashtags (`#lowercase / #brandvoice`). A trend with no
  hashtag gets its most repeated phrase (`"girl dinner"`).
- **Summary:** plain English, e.g. "84 posts in the last 12 hours on X,
  TikTok, Instagram and Reddit, about 11 times its usual pace."
- **Highlights ("Why it's trending" on the dashboard):** up to five plain
  sentences a marketer can act on, no statistics jargon:
  - the jump: "#brandvoice jumped to 26 posts in the 7 PM UTC hour (usually about 1 an hour)."
  - momentum: "Spiking right now." / "Still near its busiest…" / "Cooling off since its peak around 4 PM UTC."
  - spread: "Started on X, then spread to TikTok, Instagram and Reddit within 5 hours."
  - many voices: "79 different accounts are posting about it, so it isn't one account pushing it."
    (or, honestly, "84 posts come from just 6 accounts…")
  - beyond the hashtag: "14 more posts talk about it in their own words, without the hashtag."
    (or, for a hashtag-less trend, that its posts were grouped by meaning)

  The z-scores behind the ranking stay in `signals`, for the API and debugging.
- **Stored per trend:** everything te-api's endpoints return. That's
  `trendId`, `name`, `category`, `velocityScore`, `deltaPct`, `firstSeenAt`,
  `sources`, `sparkline`, `clusterId`, `relatedTerms`, `samplePosts` and
  `growth`, plus `summary`, `highlights`, `velocityLabel` and `signals`.
- **Trend ids are stable:** they're derived from the trend's identity
  (`term:#lowercase` or `cluster:<uuid>`), so the same trend keeps its id
  across cycles. Campaign drafts need that.

## Deviations from the design doc

- **`trend_ranking` gained `detail` (JSONB) and `signal_key`** (migration
  003), so te-api's Postgres fallback returns the same payload as the cache.
- **Not TRUNCATE + rewrite.** Each cycle, in one transaction, it clears every
  rank, upserts the new top 10, and prunes unranked rows older than 7 days
  that no campaign draft references. TRUNCATE fails once `campaign_draft_block`
  rows reference trends, and dropped-out trends must stay resolvable for
  drafts (te-api returns 404 for them because `rank IS NULL`).
- **Redis is spoken by `aggregator/redis_client.py`,** a ~100-line stdlib
  client, instead of the redis package (stdlib-first rule).

## Redis layout (AGG-6)

```
trends:v:<version>  {"version", "computedAt", "trends": [...]}   expires after 1 h
trends:current      "<version>"                                  repointed atomically each cycle
```

## Layout

- `aggregator/trigger.py`: the trigger state machine (pure, tested against scripted timelines).
- `aggregator/lock.py`: single-flight lock.
- `aggregator/ranking.py`: scoring, merge, top N.
- `aggregator/synthesis.py`: template naming and detail.
- `aggregator/repository.py`: Postgres reads/writes.
- `aggregator/cache.py`: Redis versioned push.
- `aggregator/redis_client.py`: stdlib Redis client.
- `aggregator/cycle.py`: one cycle.
- `aggregator/consumer.py`: both queues → trigger.
- `aggregator/main.py`: entrypoint.
- `tests/unit/`: 30 tests (trigger timelines, lock, scoring/merge, exact
  synthesis output, the RESP protocol, cache, cycle).
- `tests/integration/`: a seeded trend ranked #1 in Postgres and Redis with
  a stable id; spike and debounce over the real broker.
