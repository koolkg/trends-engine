# te-detector: thin wrapper. Every container is defined in
# ../te-deployment/docker-compose.yml and every target here delegates to
# ../te-deployment/Makefile (make -C te-deployment help lists everything).

DEPLOY  := ../te-deployment
COMPOSE := docker-compose -p trends-engine -f $(DEPLOY)/docker-compose.yml

.PHONY: start stop restart logs test-unit test-integration

start:
	$(MAKE) -C $(DEPLOY) up-te-detector

stop:
	$(MAKE) -C $(DEPLOY) down-te-detector

restart:
	$(MAKE) -C $(DEPLOY) restart-te-detector

logs:
	$(MAKE) -C $(DEPLOY) logs-te-detector

test-unit:
	$(MAKE) -C $(DEPLOY) test-unit-te-detector

test-integration:
	$(MAKE) -C $(DEPLOY) test-integration-te-detector
