# tests/e2e: cross-service scenarios

Four cross-service scenarios, plus te-api's contract checked
against the live ranking. They run against the real stack (te-infra +
te-normalizer + te-detector + te-cluster + te-aggregator + te-api + te-ui) from the
`e2e` container in `te-deployment/docker-compose.yml` (same compose project).

```
make -C te-deployment e2e        # starts whatever isn't running, runs these, leaves the stack up
make -C te-deployment test-e2e   # wipes all data first (make reset), runs e2e, then stops the stack if everything passed
```

`make e2e` keeps your data, so it refuses to run on a stack holding posts dated
after the fixture (e.g. after replaying te-connector's `raw_content_v2.jsonl`):
the fixture's posts would arrive late and never be ranked. Run
`make -C te-deployment reset` first, or use `test-e2e`.

## What happens

1. **Replay (once per run).** The suite publishes te-connector's 1,445-post
   fixture to `content.raw.ingested`. It waits until te-normalizer has
   archived every post, te-cluster has assigned every relevant one, and the
   pipeline queues are empty. Then it sends a `source.poll.completed` and
   waits for te-aggregator to publish a new ranking. On a stack that has
   already seen the fixture, this is quick: dedup makes the re-publish a no-op.
2. **Scenarios** (`test_<n>_*.py`, run in order):

| File | Checks |
|---|---|
| `test_1_happy_path.py` | `GET /v1/trends` returns ≥3 ranked trends in the documented shape, including the fixture's known hashtags (#lowercase/#brandvoice must be there), spanning several platforms; the top trend's detail has real fixture posts. `POST /v1/campaigns/draft-block` for the top trend returns 201, the draft is in the fake Mailchimp API holding the suggested copy, and a `campaign_draft_block` row records it. |
| `test_2_cache_miss_fallback.py` | Delete `trends:current` in Redis, and `GET /v1/trends` returns the identical feed from Postgres (`X-Trends-Source: database`), then from the repopulated cache. Same for the detail endpoint. |
| `test_3_debounce_burst.py` | Six `source.poll.completed` in 3 s produce exactly one aggregation cycle (counted as new `trends:v:*` versions in Redis). A spike score triggers a cycle in well under the 20 s debounce. |
| `test_4_irrelevant_dropped.py` | Off-topic fixture posts are archived but never normalized, clustered, scored, or shown in any trend's samples; exact reposts never get a second normalized row. |
| `test_5_api_contract.py` | Cursor pagination walks the feed exactly once; category filters partition it; every listed trend has a matching detail; 400/404 errors are well formed. |
| `test_6_ui.py` | te-ui's nginx container serves the app and its bundle, sends deep links to the app, returns te-api's feed/detail/errors unchanged through `/v1/trends`, and routes `POST /v1/campaigns/draft-block` to te-api (its 404 for an unknown trend comes back through the proxy). Doesn't need the replay; also `make -C te-deployment test-integration-te-ui`. |

The first run on a fresh stack takes a few minutes (te-cluster warms up the
model and embeds ~1,200 posts); later runs about a minute, mostly the
debounce scenario's deliberate waits.
