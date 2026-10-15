# te-connector: thin wrapper. Every container is defined in
# ../te-deployment/docker-compose.yml and every target here delegates to
# ../te-deployment/Makefile (make -C te-deployment help lists everything).

DEPLOY  := ../te-deployment
COMPOSE := docker-compose -p trends-engine -f $(DEPLOY)/docker-compose.yml

.PHONY: start stop restart logs test-unit test-integration fixtures fixtures-v2

start:
	$(MAKE) -C $(DEPLOY) up-te-connector

stop:
	$(MAKE) -C $(DEPLOY) down-te-connector

restart:
	$(MAKE) -C $(DEPLOY) restart-te-connector

logs:
	$(MAKE) -C $(DEPLOY) logs-te-connector

test-unit:
	$(MAKE) -C $(DEPLOY) test-unit-te-connector

test-integration:
	$(MAKE) -C $(DEPLOY) test-integration-te-connector

# Regenerates fixtures/raw_content.jsonl + raw_content.labels.jsonl in a
# pinned-Python container (stdlib only, deterministic for a given seed).
# Override generator flags with ARGS, e.g. make fixtures ARGS="--count 3000"
fixtures:
	docker run --rm -v "$(CURDIR)":/app -w /app python:3.12-slim \
		python fixtures/generate_fixture.py $(ARGS)

# Realistic-volume fixture (fixtures/raw_content_v2*.jsonl): ~41k posts over 5 days,
# mostly noise. Replay with CONNECTOR_FIXTURE_PATH=fixtures/raw_content_v2.jsonl.
fixtures-v2:
	docker run --rm -v "$(CURDIR)":/app -w /app python:3.12-slim \
		python fixtures/generate_fixture_v2.py $(ARGS)
