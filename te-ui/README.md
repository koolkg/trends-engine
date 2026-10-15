# te-ui

The marketer-facing dashboard. A React +
TypeScript single-page app that shows te-aggregator's ranked trends (through
te-api), explains each one, and sends it to a draft Mailchimp campaign (te-api
does the Mailchimp call).
It ports the design doc's UX Prototype tab to real components.

## Quick start

```
make start              # http://localhost:3000 (starts te-api and infra if needed)
make open               # open it in your browser
make logs / make stop
make test-unit          # type-check + Jest / React Testing Library, in a container
make test-integration   # the real nginx container: app, deep links, /v1 proxying
```

Node and `node_modules` only exist inside the image; nothing runs on your Mac's
Node. The container is defined in `../te-deployment/docker-compose.yml`.

## What it does

- **Ranked list** (UI-2): rank, name, category, a 12-hour posts-per-hour
  sparkline and the change vs. the trend's usual rate. "Spiking" badge when
  te-aggregator labels it a spike. "Show more" follows te-api's `nextCursor`.
- **Category chips:** All / Email Marketing / Social / Ecommerce / Content,
  sent to te-api as `?category=`.
- **Detail:** summary, first seen, platforms, growth chart, **Why it's
  trending** (te-aggregator's plain-language highlights: the jump, momentum,
  spread across platforms, how many different accounts, talk beyond the
  hashtag), top sample posts, and related tags and phrases. The raw scores
  behind the ranking aren't shown on the dashboard.
- **Campaign block** (UI-3): shows exactly what "Insert into campaign" will put
  into Mailchimp (subject line, preview text, headline and the content block)
  as an email preview, before anything is sent. A tone picker
  (Casual / Professional / Expert: the voice of the copy) rewrites it (`POST
  /v1/campaigns/draft-block/preview`). **Insert into campaign** sends
  `POST /v1/campaigns/draft-block` `{trendId, campaignId: null, tone}` (the
  design doc's request); te-api creates the draft campaign in Mailchimp (the
  fake one in the PoC). The block has no button: the marketer adds their own
  call to action, and where it goes, in Mailchimp.
  Afterwards it shows what was inserted, the campaign id
  and an **Open the draft** link to the email in Mailchimp; te-api's error
  message otherwise (e.g. "Mailchimp can't be reached right now").
- **Loading and error states** (UI-4): loading text while te-api answers (a
  cache miss reads Postgres), an error with "Try again" for the list and the
  detail, and "aged out of the ranking" when a trend's detail is a 404.
- **Freshness:** "Updated N min ago" from te-api's `X-Trends-Version` (the
  ranking's computed-at time). Re-reads the ranking every 60 s and keeps the
  selected trend if it's still ranked.

## How it's served

`Dockerfile` has two stages:

1. `build` (node:20-alpine): `npm ci`, then `npm run build` (webpack + Babel).
   The unit tests run in this stage's image (compose service `te-ui-test`).
2. nginx:1.27-alpine serves `dist/` and proxies (`nginx.conf`):
   - `/v1/...` (the feed, a trend, the campaign POST) → `te-api:8080`
   - every other path → `index.html` (single-page app)

The browser only ever talks to `localhost:3000`, so there's no CORS and no API
URL baked into the bundle. te-api is resolved per request, so nginx keeps
serving the app while te-api restarts; those calls get a 502, shown as
"The trends service can't be reached right now".

## Layout

- `src/api.ts`: te-api calls and error mapping.
- `src/types.ts`: response shapes (te-api/README.md).
- `src/format.ts`: labels, deltas, times.
- `src/App.tsx`: page state (category, paging, selection, refresh).
- `src/components/`: `TrendList`, `TrendDetail`, `CampaignBlock`, `CategoryChips`, `Sparkline`, `Toast`.
- `tests/`: 34 Jest + React Testing Library tests against fixture data in te-api's shapes, with `fetch` faked.
- `webpack.config.js`, `babel.config.js`, `tsconfig.json`: build. `npm start` (webpack-dev-server on :3000,
  proxying /v1 to localhost:8080) exists for working outside Docker, but the stack doesn't use it.

## Tooling choice

Same stack as stratus-webapp (webpack 5 + Babel, Jest + React Testing
Library), as a single app instead of micro-frontends. Plain CSS ported from
the prototype instead of Tailwind: one page doesn't need it.
