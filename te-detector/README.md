# te-detector

**Hashtag velocity.** It consumes
`content.normalized` (`q.detector.content-normalized`), counts each hashtag per
time window, compares each count to that hashtag's own history, and reports
the ones that jump.

```
content.normalized ─▶ extract hashtags ─▶ count per hour (by postedAt) ─▶ window closes
      ─▶ z-score vs. each tag's own baseline ─▶ term_frequency_snapshot + trend.score.upserted
```

## Quick start

Everything runs in the `te-detector:local` image (python:3.12-slim with a venv
inside), defined in `../te-deployment/docker-compose.yml`.

```
make start / make stop / make logs
make test-unit          # no infra
make test-integration   # real Postgres + RabbitMQ
```

## How it decides something is trending

For every hashtag the detector keeps a running average of its per-window
count (an EWMA), plus a running average of how much that count normally
wobbles. When a window closes, each tag gets a z-score:

```
z = (count this window − usual count) / sqrt(usual wobble + usual count + 1)
```

So "how many normal swings above its own normal is this?" A tag that's always
busy (e.g. `#marketing`) doesn't rank just for being big, and a rare tag that
suddenly appears ten times does. The `+ usual count + 1` keeps tiny counts from
looking dramatic (0 → 2 mentions scores 2, not infinity).

| z | velocityLabel |
|---|---|
| ≥ `DETECTOR_Z_THRESHOLD` (3.0) | `spike`, which wakes te-aggregator immediately |
| ≥ 1 | `rising` |
| ≤ −1 | `falling` |
| otherwise | `steady` |

## Decisions that differ from the design doc

All were agreed on in the design review:

- **Hashtags only** (no free-text keywords or named entities). Simulated on
  the fixture, keywords split each trend into dozens of word-pair "terms" and
  flagged generic words. Hashtags were clean. Phrases and trends without a
  hashtag are te-cluster's job.
- **1-hour windows, keyed on `postedAt`**, not 5 minutes by arrival time. At
  ~4 relevant posts per 5 minutes, 5-minute counts are mostly 0 or 1 and the
  z-scores are noise. Event time (`postedAt`) is used because te-connector
  replays a day of posts in a couple of hours.
- **Only tags with count ≥ `DETECTOR_MIN_COUNT` are written/published.** The
  rest still update their baseline silently. On the fixture that's 57 rows a
  day instead of ~20,000.
- **One message type.** `trend.score.upserted` with `velocityLabel: "spike"`
  is the "wake up now" signal; there's no separate spike event (DET-6).
- **Warmup:** the first `DETECTOR_WARMUP_WINDOWS` (2) windows only build
  baselines. At cold start every tag is new and would look like a spike.
- **Ignored tags:** platform boilerplate and catch-alls (`#fyp`, `#foryou`,
  `#marketing`, `#smallbusiness`, …). See `detector/config.py`; override with
  `DETECTOR_IGNORE_TAGS`.

## Windows: when they close

A window is scored once a post from a later window arrives. Posts arrive
roughly in time order, so that's enough. A post for an already-closed window
is "late": it's counted (logged at shutdown) and ignored. If the queue is
quiet for `DETECTOR_IDLE_FLUSH_SECONDS` (120), the oldest open window is
closed anyway, so the last hour of a replay still gets scored. Empty hours
in between are closed too, so a tag's baseline decays while it's silent.

## On the fixture

te-normalizer's output for te-connector's fixture (1,207 posts,
`tests/fixtures/normalized_stream.jsonl`) with default settings gives 60
scores, 4 of them spikes. All 4 are real trend tags:

| window | tag | count | baseline | z |
|---|---|---|---|---|
| 18:00 | #lowercase | 9 | 0.61 | 5.64 |
| 18:00 | #emailmarketing | 11 | 2.55 | 3.69 |
| 19:00 | #brandvoice | 26 | 1.02 | 14.84 |
| 19:00 | #lowercase | 21 | 3.12 | 4.06 |

Known limit: z-scores catch **sudden** jumps. A trend that grows steadily
(the fixture's "dupe culture") moves its own baseline up as it goes, so it
shows as `rising` at most, never `spike`. te-cluster's growth score and
te-aggregator's ranking have to carry those.

## Messages and rows

`trend.score.upserted` (exactly the design's event schema):

```json
{"term": "#lowercase", "windowStart": "2026-09-07T18:00:00Z", "windowEnd": "2026-09-07T19:00:00Z",
 "frequency": 10, "baselineFrequency": 0.16, "zScore": 8.65, "velocityLabel": "spike"}
```

…plus one matching `term_frequency_snapshot` row (written first, then published).

## State and delivery

- Window counts and baselines live **in memory**. They survive a broker
  reconnect but not a process restart: after a restart the detector warms up
  again. Fine for the PoC; production would keep them in Redis.
- If Postgres or RabbitMQ fails while a window's scores are going out, the
  scores are kept and retried with the next message rather than lost. A failure
  between the DB write and the publish can produce a duplicate snapshot row on
  retry.
- Input is acked after processing; a redelivered message is counted twice.
  Malformed messages are acked and dropped.

## Layout

- `detector/hashtags.py`: extraction (DET-2).
- `detector/scoring.py`: baseline and z-score (DET-4/5).
- `detector/detector.py`: event-time windows and closing (DET-3). Pure logic.
- `detector/handler.py`: message parsing; persist, then publish (DET-6).
- `detector/store.py`: `term_frequency_snapshot` writes.
- `detector/consumer.py`: pika loop with the idle flush (DET-1).
- `detector/main.py`: entrypoint.
- `tests/unit/`: 34 tests, including hand-computed EWMA values, the
  exact-threshold boundary, and the full fixture stream.
- `tests/integration/`: a burst through the real broker and DB, plus the idle flush.
