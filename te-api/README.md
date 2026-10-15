# te-api

The dashboard-facing API. It serves the
ranked trend feed te-aggregator publishes, from Redis, and falls back to
Postgres when the cache is empty (it never recomputes anything). Its
campaigns module turns a trend into a draft Mailchimp campaign.

```
GET  /v1/trends?category=&limit=&cursor=   ranked feed, one page
GET  /v1/trends/{trendId}                  one trend's detail
POST /v1/campaigns/draft-block             trend -> draft content block in a Mailchimp campaign
POST /v1/campaigns/draft-block/preview     the copy that would be inserted (no side effects)
GET  /healthz                              liveness (used by the container healthcheck)
```

## Quick start

Runs as its own container (`te-api:local`, python:3.12-slim + venv), defined in `../te-deployment/docker-compose.yml`.

```
make start              # http://localhost:8080/v1/trends  (other containers: http://te-api:8080)
make smoke              # print the top 5 from your Mac
make stop / make logs
make test-unit          # no infra
make test-integration   # real Postgres + Redis + te-infra's fake Mailchimp API
```

## Responses

`GET /v1/trends`: the design doc's list schema, plus `rank`, `velocityLabel`
and `summary` (te-ui shows them):

```json
{"items": [{"trendId": "3f0c…", "name": "#lowercase / #brandvoice", "category": "social",
            "velocityScore": 2.83, "deltaPct": 984, "firstSeenAt": "2026-09-07T17:02:11Z",
            "sources": ["x", "tiktok", "instagram", "reddit"], "sparkline": [0, 0, 1, 2, 5, 9, 31, 24, 8, 3, 1, 0],
            "rank": 4, "velocityLabel": "rising", "summary": "…"}],
 "nextCursor": "eyJvZmZzZXQiOjIwfQ"}
```

- `category`: one of `email`, `social`, `ecommerce`, `content` (anything else is 400).
- `limit`: 1–100, default 20.
- `cursor`: the opaque `nextCursor` from the previous page; `null` on the last page.

`GET /v1/trends/{trendId}`: the doc's detail schema (`trendId`, `clusterId`,
`relatedTerms`, `samplePosts[{text, source, postedAt}]`, `growth[{date, score}]`)
plus the list fields, `highlights` (why it's trending, in plain words, for the dashboard) and
`signals` (the hashtag/cluster scores behind the ranking, for debugging).

Errors are `{"error": "<code>", "message": "…"}`: 400 for a bad
`category`/`limit`/`cursor`, 404 for an unknown or aged-out trend (no longer
ranked), 405 for an unsupported method, 500 for anything unexpected (details only in the log).

Headers: `X-Trends-Source: cache | database` and `X-Trends-Version: <ranking version>`.

`POST /v1/campaigns/draft-block` (Flow 2 steps 8–11):

```json
request  {"trendId": "3f0c…", "campaignId": null, "tone": "casual"}
response 201 {"blockId": "9b1e…", "suggestedCopy": "Lowercase is everywhere right now, …", "status": "draft",
              "trendId": "3f0c…", "campaignId": "a1b2c3d4e5", "tone": "casual",
              "subjectLine": "Lowercase is having a moment ✨", "previewText": "Everyone's talking about lowercase. …",
              "headline": "Have you noticed lowercase?",
              "previewUrl": "http://localhost:8090/archive/a1b2c3d4e5"}
```

`POST /v1/campaigns/draft-block/preview` takes the same body and returns the
same copy fields (200, no Mailchimp call, no row), so te-ui shows the marketer
exactly what will go into the campaign before they insert it.

- `campaignId`: `null` creates a new draft campaign; a Mailchimp campaign id adds the block to that one.
- `tone`: the voice of the copy: `casual` (default), `professional` or `expert` (numbers-first)
  (`playful`, the design doc's example value, means `casual`).
- `blockId` is the new `campaign_draft_block` row's id; the rest beside the doc's three fields are
  extras: everything that went into the campaign, and `previewUrl`, Mailchimp's web view of the draft
  (the campaign's `long_archive_url`).
- Errors: 400 (`bad_json`, `bad_trend_id`, `bad_campaign_id`, `bad_tone`), 404 (`trend_not_found`,
  `campaign_not_found`), 413 (body over 64 KB), 502 (`mailchimp_unavailable`: can't reach it;
  `mailchimp_error`: it refused, e.g. a bad key).

## Campaigns (Mailchimp integration)

A module of te-api, not its own service (a design decision). For each request:

1. Look the trend up in `trend_ranking` (ranked or just aged out; 404 if unknown).
2. Write the copy for the tone (`api/copywriter.py`): subject line, preview
   text, headline and the suggested block (no button: the marketer adds their
   own call to action in Mailchimp), from the trend's
   name, how far above normal it is, its platforms and related terms. A
   template, not an LLM (same reason as te-aggregator's naming). Sample posts
   are never quoted, since they're other people's words.
3. Call Mailchimp's Marketing API (`api/mailchimp.py`): `POST /campaigns` (a
   draft `regular` campaign, no audience yet) or `GET /campaigns/{id}`, then
   `PUT /campaigns/{id}/content` with the HTML and plain text.
4. Insert the `campaign_draft_block` row and return its id.

Mailchimp is called before the row is written, so a failed insert can leave
a harmless draft in Mailchimp, never a row pointing at a draft that doesn't exist.

In the PoC Mailchimp is te-infra's `mailchimp-fake` (`MAILCHIMP_API_URL`
`http://mailchimp-fake:8090/3.0`). It speaks the real API's requests and
responses, so the real thing is `MAILCHIMP_API_URL=https://<dc>.api.mailchimp.com/3.0`
plus a real `MAILCHIMP_API_KEY` (no code change). See what te-api created:

```
curl -s -u any:fake-key-us1 localhost:8090/3.0/campaigns | python3 -m json.tool
```

## Cache-miss fallback (API-2)

1. Read `trends:current` → version → `trends:v:<version>` (written by te-aggregator).
2. If either key is missing (nothing cached yet, or the version expired),
   read the ranked rows from `trend_ranking`. te-aggregator stores each
   trend's full payload in its `detail` column, so this is the same data.
3. Write it back to Redis in te-aggregator's layout. The pointer is only set
   if it's absent or points at an expired version, so a newer ranking that
   te-aggregator just published always wins.
4. If Redis itself is down, it serves from Postgres and keeps going.

## Deviations / notes

- `firstSeenAt` and `growth[].date` are full ISO timestamps (the doc shows
  dates). Growth is hourly, so dates alone would collapse it.
- Pagination is offset-based (the doc's example cursor decodes to
  `{"offset":20}`). If a new ranking lands between page 1 and page 2, page 2
  comes from the new one. That's acceptable for a 10-trend feed.
- Stdlib only for HTTP (`http.server`, threaded) and Redis
  (`api/redis_client.py`); psycopg2 for Postgres.

## Layout

- `api/trends.py`: feed/detail logic, response shaping, cursors, validation (API-2/3/4).
- `api/cache.py`: Redis read + repopulate.
- `api/repository.py`: Postgres: the feed fallback, the trend lookup, the draft-block insert.
- `api/campaigns.py`: `POST /v1/campaigns/draft-block` (validation, Mailchimp calls, error mapping).
- `api/copywriter.py`: suggested copy per tone.
- `api/mailchimp.py`: Mailchimp Marketing API client (stdlib).
- `api/server.py`: routing and the HTTP server (API-1).
- `api/main.py`: entrypoint.
- `tests/unit/`: 39 tests, including the real HTTP server on an ephemeral port with in-memory
  backends, and the Mailchimp client against a stub HTTP server.
- `tests/integration/`: real Postgres + Redis + the fake Mailchimp API, with an isolated
  Redis key prefix and its own trend row, so the live feed is never touched.
- Cross-service scenarios (cache miss with the whole stack running, etc.) are in `../tests/e2e`.
