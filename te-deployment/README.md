# te-deployment

Runs the whole trends-engine stack. Everything is in two files here:

- **`docker-compose.yml`**: every container (Postgres, RabbitMQ, Redis, and
  each `te-*` service, built from its own directory) as one compose project.
  They share one network and reach each other by service name.
- **`Makefile`**: the commands. The repo root's Makefile forwards to this one,
  so run them from the root (`make up`) or here (`make -C te-deployment up`).

```
make up              start everything (builds images as needed; infra first)
make up v2           same, replaying the 5-day v2 fixture (generated on first use)
make down            stop everything (data kept)
make restart         down + up
make status          what's running
make logs [name ...] follow logs: e.g. make logs poller aggregator (no name = all)
make reset           stop AND wipe all data (fresh start, e.g. before a demo replay)

make up-te-api / down-te-api / restart-te-api       one service (any te-*, or te-infra)
make fixtures-v2     regenerate the v2 fixture        make fixtures   regenerate v1

make test-unit       every service's unit tests
make test-integration  te-infra smoke test + every service's integration tests
make e2e             end-to-end suite on the current data (starts the stack if needed, leaves it running)
make test-e2e        reset, e2e, then stops the stack if it passed
make test            all of the above
```

- **Start order:** `depends_on` handles it. Every app service waits for
  Postgres, RabbitMQ and Redis to be healthy, and for two one-shot setup
  containers to finish: `postgres-migrate` applies the schema migrations and
  `rabbitmq-init` loads the queues and bindings. Starting any single service
  brings up whatever it needs first.
- **Settings:** copy `.env.example` to `.env` here and change what you need.
  Every value has a default.
- **Data** lives in the `te-infra_pgdata`, `te-infra_rabbitmqdata` and
  `te-infra_redisdata` volumes, the same ones te-infra's old compose file used.
  `make down` keeps them; `make reset` deletes them.
- **Endpoints on your Mac:** te-ui at http://localhost:3000, te-api at http://localhost:8080/v1/trends,
  the fake Mailchimp API at http://localhost:8090/3.0 (`curl -u any:fake-key-us1 …/campaigns`),
  RabbitMQ UI at http://localhost:15672, Postgres on localhost:5432.

Each `te-*` directory keeps a small Makefile (`start`, `stop`, `logs`,
`test-unit`, `test-integration`, plus service extras like `eval` or `rank`)
that calls into this one.
