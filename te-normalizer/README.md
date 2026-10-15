# te-normalizer

Consumes `content.raw.ingested` (from
`q.normalizer.content-raw-ingested`), and for each post:

1. **Cleans** the text (NORM-2): strips markup, URLs and control/zero-width
   characters, normalizes Unicode, collapses whitespace. Hashtags, @mentions,
   emoji and casing are kept for te-detector and te-cluster.
2. **Classifies** relevance and category (NORM-4, decision NORM-9 below):
   the all-MiniLM-L6-v2 model decides by meaning, with the keyword/rule table
   as backup and validator. Categories: `email`, `social`, `ecommerce`, `content`.
3. **Stores** it (NORM-5): every post gets a `raw_content_item` row (the
   archive). Relevant posts also get a `normalized_content_item` row, in the
   same transaction.
4. **Dedups** it (NORM-3): see the decision below.
5. **Publishes** `content.normalized` (NORM-6) for relevant, non-duplicate
   posts only. It fans out to both `q.detector.*` and `q.cluster.*`. Dropped
   posts are counted in the logs and never published.

## Quick start

Everything runs in the `te-normalizer:local` image (python:3.12-slim with a
venv inside); nothing uses the host's Python. The container is defined in
`../te-deployment/docker-compose.yml`; `start` and `test-integration` bring up
the infra they need.

```
make start              # build + run the service container
make logs               # follow its logs (running totals every 100 messages)
make stop
make test-unit          # no infra needed
make test-integration   # against the stack's Postgres + RabbitMQ
make eval               # classifier scores against te-connector's labeled fixture
```

## Decision: dedup approach (NORM-8)

**Exact hash of a normalized key**, not near-duplicate matching. The key is
the cleaned text, lowercased, with hashtags, @mentions, emoji and punctuation
removed. Prices and percentages are kept. The unique index on
`normalized_content_item.dedup_hash` enforces it: the insert itself is the
dedup check (`ON CONFLICT DO NOTHING`), so there's no race and no extra state.

- It catches reposts and cross-posts that differ only in hashtags, emoji,
  casing or punctuation. On the fixture that's all 11 exact reposts and 26 of
  35 cross-posts; the misses are Reddit versions with an added "Discussion:"
  or "Thoughts?".
- It deliberately doesn't merge posts that only *share wording*. People
  echoing a phrase is the trend signal a near-dup filter would destroy.
- The function lives alone in `normalizer/dedup.py`, so swapping in a simhash
  or shingle check later is a one-file change.

Dedup is global and permanent: a second pass over the same fixture yields
nothing new downstream (verified: a full replay came back all duplicates or
irrelevant). So te-connector's `CONNECTOR_LOOP_FIXTURE=true` only produces
fresh content on its first pass.

## Decision: model first, keywords as backup and validator (NORM-9)

The keyword table only knows the words someone typed into it, so new slang
and new trends read as off-topic. The service now runs the same embedding
model as te-cluster (`normalizer/semantic.py`):

- Each category is described by a few example sentences, plus off-topic
  examples ("what I cooked for dinner tonight"). They're turned into vectors
  once at startup. A post goes to the category with the most similar example.
- `normalizer/relevance.py` combines the two classifiers:
  - **model sure** (similarity ≥ `NORMALIZER_MODEL_SURE`, default 0.40, and at
    least `NORMALIZER_MODEL_MARGIN` 0.05 above the off-topic examples): keep,
    with the model's category;
  - **model says no** (similarity < `NORMALIZER_MODEL_FLOOR` 0.25, or the
    off-topic examples are clearly closer): drop, unless the keyword table is
    strongly sure (score 1.0), then keep with the keyword category;
  - **model unsure** (in between): keep only if the keyword table agrees;
  - **model unavailable** (can't load, or fails on a post): keyword table alone.
- Every published message carries `classifiedBy` (`model`, `keywords-check`,
  `keywords-override`, `keywords`, `keywords-fallback`); the shutdown log
  counts decisions and model/keyword disagreements, which are the posts worth
  labelling for a trained classifier later.
- `NORMALIZER_CLASSIFIER=keywords` switches back to the table alone.

The category descriptions live in code next to the keyword table, not in a
config file: they change with the code and `make eval` measures every change.
The thresholds are env vars because they're the knobs `make eval --sweep`
tunes. Cost: the image now carries CPU PyTorch and the ~90 MB model, like
te-cluster's.

## Classifier accuracy

The numbers below are the keyword table's. `make eval` now prints keywords,
model alone and combined side by side (model variants need the image, which
has the model baked in); rerun it after any change to the descriptions or thresholds.

`make eval`, on te-connector's 1,445-post fixture (realistic wording, see
te-connector/README) at the default threshold 0.5:

- relevance: precision 1.000, recall 0.976. Every off-topic post is dropped,
  including look-alikes (food "girl dinner", pet "POV" videos, "sold out"
  concert tickets). About 2% of on-topic posts carry no marketing words at all
  ("POV: it's 7am and the planner is the only reason you're awake") and are
  dropped too.
- category: 91% on trend posts. "girl dinner" posts are the weak spot (57%):
  many are about newsletters or posts, so they land under email or social.
  The trend's category comes from its posts' majority, so it still reads as content.

The keywords are general marketing and shopping vocabulary rather than phrases
from the fixture. Catchphrases that also show up in everyday off-topic posts
("girl dinner", "POV") carry little weight on their own. Real posts would
still score lower than this: it's the rule-based stand-in for a trained
classifier, a known PoC limitation.

`scripts/export_normalized_stream.py` writes this service's output for the
fixture (1,207 posts) as the labeled stream te-detector's and te-cluster's
offline tests and evals use. Rerun it after regenerating the fixture.

## `content.normalized` message

Not in the design's event contract, so defined here:

```json
{
  "normalizedId": 1234,          // normalized_content_item.id (te-cluster's FK target)
  "rawContentId": 5678,          // raw_content_item.id
  "sourceId": "src_x_search",
  "contentId": "1800000000000000001",
  "cleanedText": "found the $12 dupe for the $58 serum 🔥 #dupe",
  "category": "ecommerce",
  "relevanceScore": 0.75,
  "author": "@beautyonabudget",
  "postedAt": "2026-09-07T11:58:00Z",
  "processedAt": "2026-09-07T12:00:05Z",
  "engagement": {"likes": 41000, "shares": 2100, "comments": 640}
}
```

## Delivery semantics

At-least-once. The DB transaction commits, then `content.normalized` is
published, then the input is acked. A crash before the ack redelivers the
input: that writes a second `raw_content_item` row (the archive has no unique
key), and the post is then treated as a duplicate and not republished. A crash
between commit and publish can therefore lose one post downstream. That's
acceptable for the PoC; a transactional outbox is the production fix.

Malformed messages (bad JSON, missing `sourceId`/`contentId`/`rawText`) are
acked and dropped with a warning. Any other error (DB or broker down) nacks
and requeues the message after a short backoff, and the service reconnects
on its own.

## Layout

- `normalizer/cleanup.py`, `dedup.py`, `classifier.py`: pure functions.
- `normalizer/semantic.py`: the model classifier (category descriptions + MiniLM).
- `normalizer/relevance.py`: combines model and keywords into one decision.
- `normalizer/pipeline.py`: parse → clean → relevance → store → publish. The
  store and publisher are injected, so it's unit-tested with fakes.
- `normalizer/store.py`: Postgres writes (psycopg2).
- `normalizer/consumer.py`: the pika consume loop and the publisher on the same channel.
- `normalizer/main.py`: entrypoint with reconnect and a clean SIGTERM stop.
- `tests/unit/`: 46 tests (3 need the real model and run in the container), including the "labeled slice of the
  fixture" check (`tests/fixtures/labeled_slice.jsonl`, the first 5 posts
  of each trend/evergreen/noise, not hand-picked).
- `tests/integration/`: relevant, off-topic and cross-post-duplicate posts
  through the real store and broker, plus the real consumer loop on a
  throwaway queue.
- `scripts/eval_classifier.py`: the `make eval` report.
