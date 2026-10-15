# te-aggregator: thin wrapper. Every container is defined in
# ../te-deployment/docker-compose.yml and every target here delegates to
# ../te-deployment/Makefile (make -C te-deployment help lists everything).

DEPLOY  := ../te-deployment
COMPOSE := docker-compose -p trends-engine -f $(DEPLOY)/docker-compose.yml

.PHONY: start stop restart logs test-unit test-integration rank

start:
	$(MAKE) -C $(DEPLOY) up-te-aggregator

stop:
	$(MAKE) -C $(DEPLOY) down-te-aggregator

restart:
	$(MAKE) -C $(DEPLOY) restart-te-aggregator

logs:
	$(MAKE) -C $(DEPLOY) logs-te-aggregator

test-unit:
	$(MAKE) -C $(DEPLOY) test-unit-te-aggregator

test-integration:
	$(MAKE) -C $(DEPLOY) test-integration-te-aggregator

# Run one aggregation cycle now and print the ranking (needs the stack up).
rank:
	$(COMPOSE) build te-aggregator
	$(COMPOSE) run --rm te-aggregator python -m aggregator.run_once
