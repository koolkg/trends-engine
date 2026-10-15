# te-infra

Local RabbitMQ, Postgres (with pgvector),
Redis, and a fake Mailchimp API for the rest of the trends-engine services.
Not application code: this directory is schema, topology, the Mailchimp
stand-in and the smoke test. The
containers themselves are defined in `../te-deployment/docker-compose.yml`,
alongside every service, in one compose project (one network).

## Quick start

```
make start              # infra only: postgres, redis, rabbitmq, mailchimp-fake + init containers
make test-unit          # the fake Mailchimp API's tests
make test-integration   # smoke test in its own container
make stop
```

`make -C ../te-deployment up` starts infra and every service together.
RabbitMQ's management UI is at http://localhost:15672 (trends / trends_dev_pw
unless overridden in `../te-deployment/.env`).

## What's here

- `postgres/init/001_schema.sql`: the design's data model, applied by the postgres image's
  entrypoint. **This only runs on first init**, i.e. when the
  `te-infra_pgdata` volume is empty. If you edit it after the volume
  exists, `make -C ../te-deployment reset` (wipes local data) and start again.
- `postgres/init/002_*.sql`, `003_*.sql`: idempotent migrations. The
  one-shot `postgres-migrate` container applies them on every start, and
  every service waits for it to finish.
- `rabbitmq/definitions.json`: the `trends.topic` exchange, queues and
  bindings. The one-shot `rabbitmq-init` container imports it through the
  management API on every start (idempotent). It's deliberately not loaded
  at boot via `load_definitions`: that makes RabbitMQ skip creating the
  default vhost and user, so the node fails to come up. `content.normalized`
  fans out to two queues (te-detector and te-cluster each get a copy).
- `mailchimp-fake/`: a stand-in for Mailchimp's Marketing API (v3): a stubbed
  adapter. te-api calls it exactly as it would
  call `https://<dc>.api.mailchimp.com/3.0`: `GET /3.0/ping`,
  `POST/GET /3.0/campaigns`, `GET /3.0/campaigns/{id}`,
  `PUT/GET /3.0/campaigns/{id}/content`, HTTP Basic auth with the API key as
  password, Mailchimp's problem-JSON errors. Each campaign's
  `long_archive_url` points at `/archive/{id}`, a page showing the draft
  email as stored (te-ui's "Open the draft"). In memory (a restart forgets
  every campaign). On your Mac at http://localhost:8090/3.0, e.g.
  `curl -s -u any:fake-key-us1 localhost:8090/3.0/campaigns`.
- `scripts/smoke_test.py` (+ `scripts/Dockerfile`): the Phase 0 integration
  test, run as the compose `te-infra-smoke` service: schema present, Redis
  round-trips, a message flows through each topic end to end, and the fake
  Mailchimp API answers `/ping`.
