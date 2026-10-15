# te-cluster: thin wrapper. Every container is defined in
# ../te-deployment/docker-compose.yml and every target here delegates to
# ../te-deployment/Makefile (make -C te-deployment help lists everything).

DEPLOY  := ../te-deployment
COMPOSE := docker-compose -p trends-engine -f $(DEPLOY)/docker-compose.yml

.PHONY: start stop restart logs test-unit test-integration eval

start:
	$(MAKE) -C $(DEPLOY) up-te-cluster

stop:
	$(MAKE) -C $(DEPLOY) down-te-cluster

restart:
	$(MAKE) -C $(DEPLOY) restart-te-cluster

logs:
	$(MAKE) -C $(DEPLOY) logs-te-cluster

test-unit:
	$(MAKE) -C $(DEPLOY) test-unit-te-cluster

test-integration:
	$(MAKE) -C $(DEPLOY) test-integration-te-cluster

# Threshold tuning: cluster quality on the labeled fixture at several thresholds.
eval:
	$(COMPOSE) build te-cluster
	$(COMPOSE) run --rm --no-deps te-cluster python scripts/eval_clusters.py
