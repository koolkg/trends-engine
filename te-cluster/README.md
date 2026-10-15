# te-cluster

**Semantic clustering + cluster growth.**
It consumes `content.normalized` (`q.cluster.content-normalized`), turns each
post into a 384-number "meaning vector" with all-MiniLM-L6-v2, files it into
the nearest existing cluster (or starts a new one), and scores how fast each
cluster is growing. This is what catches a trend *before* it has a shared
hashtag or phrase, such as the fixture's "girl dinner" posts, which have no
hashtag at all.

```
micro-batch of posts ─▶ one MiniLM pass ─▶ nearest centroid? ──join (cos ≥ 0.72)──▶ update centroid
                                                     └──seed──▶ new cluster
   ─▶ new members per cluster per hour ─▶ z-score vs. the cluster's own baseline
   ─▶ content_cluster_assignment + cluster_centroid + cluster_velocity_snapshot + cluster.score.upserted
```

## Quick start

```
make start / make stop / make logs
make test-unit          # first build: ~1 GB (CPU torch + model), then cached
make test-integration   # real model + Postgres + RabbitMQ
make eval               # cluster quality on the labeled fixture at several thresholds
```

The container is defined in `../te-deployment/docker-compose.yml`. Its migration
(002) and RabbitMQ binding are applied by the stack's init containers on every start.

## How it works

- **Embedding (CLU-2, decision CLU-9 = the real model):** sentence-transformers
  on CPU PyTorch, text truncated to 128 tokens, vectors L2-normalized (so
  cosine similarity is a dot product). It runs once per micro-batch, not per
  post: up to 64 messages or a 50 ms wait, as the design doc specifies. The
  model is downloaded at build time and the container runs offline.
- **Join-or-seed (CLU-3/4):** brute-force cosine against every active
  centroid, held in memory as one numpy matrix (hundreds of clusters at most).
  Similarity ≥ `CLUSTER_SIMILARITY_THRESHOLD` (0.72, the design doc's starting
  point) joins the nearest cluster, and its centroid moves toward the post:
  `normalize(c + (v − c)/(n + 1))`. Otherwise the post seeds a new cluster.
- **Growth score (CLU-5):** new members per cluster per hour (by `postedAt`),
  with the same EWMA baseline and z-score as te-detector, so te-aggregator
  merges like with like. One-off clusters never reach `CLUSTER_MIN_COUNT`, so
  they never surface; that's the design's "velocity scoring does the filtering".
- **Cold eviction:** a cluster with no new member for 24 h (post time) leaves
  the index and its `cluster_centroid` row is deleted. Its assignment history
  is kept.
- **Restart:** centroids are reloaded from `cluster_centroid` at startup. Growth
  baselines are in memory and re-warm, as in te-detector.

## Decisions / deviations

- **New table `cluster_velocity_snapshot`** (te-infra `002_cluster_velocity.sql`).
  The design doc has nowhere to store cluster z-scores; this mirrors
  `term_frequency_snapshot`.
- **New routing key `cluster.score.upserted`**, bound to te-aggregator's
  existing `q.aggregator.trend-score-upserted` queue. Same message shape as
  `trend.score.upserted`, but with `clusterId` instead of `term`, plus `memberCount`.
- **Hourly windows** instead of the doc's 5 minutes, matching te-detector (see its README).
- **pgvector is used only for storage.** Lookup is in memory (CLU-3);
  the HNSW index in the schema is unused at PoC scale.
- **Redelivery is safe:** posts that already have a cluster are skipped, so a
  post is in exactly one cluster.

## `cluster.score.upserted`

```json
{"clusterId": "6f1c…", "windowStart": "2026-09-07T18:00:00Z", "windowEnd": "2026-09-07T19:00:00Z",
 "frequency": 14, "baselineFrequency": 1.2, "zScore": 5.4, "velocityLabel": "spike", "memberCount": 31}
```

## Layout

- `cluster/embedder.py`: MiniLM.
- `cluster/centroids.py`: index, join-or-seed, eviction.
- `cluster/velocity.py` + `cluster/scoring.py`: growth windows and z-score.
- `cluster/handler.py`: one micro-batch, end to end.
- `cluster/store.py`: Postgres.
- `cluster/consumer.py`: micro-batching pika loop.
- `cluster/main.py`: entrypoint.
- `tests/unit/`: 24 tests (2 need the real model and run in the container).
  They use `tests/fake_embedder.py`, a deterministic bag-of-words stand-in,
  for the clustering logic.
- `tests/integration/`: paraphrases cluster together, unrelated posts seed new
  clusters; a growth window is scored, stored and published.
- `scripts/eval_clusters.py`: `make eval`.
