# te-api: thin wrapper. Every container is defined in
# ../te-deployment/docker-compose.yml and every target here delegates to
# ../te-deployment/Makefile (make -C te-deployment help lists everything).

DEPLOY  := ../te-deployment
COMPOSE := docker-compose -p trends-engine -f $(DEPLOY)/docker-compose.yml

.PHONY: start stop restart logs test-unit test-integration smoke campaigns

start:
	$(MAKE) -C $(DEPLOY) up-te-api

stop:
	$(MAKE) -C $(DEPLOY) down-te-api

restart:
	$(MAKE) -C $(DEPLOY) restart-te-api

logs:
	$(MAKE) -C $(DEPLOY) logs-te-api

test-unit:
	$(MAKE) -C $(DEPLOY) test-unit-te-api

test-integration:
	$(MAKE) -C $(DEPLOY) test-integration-te-api

# Quick look at the live feed from your Mac (te-api running).
smoke:
	@curl -fsS "http://localhost:$${API_HOST_PORT:-8080}/v1/trends?limit=5" | python3 -m json.tool

# What te-api has put into the (fake) Mailchimp so far: draft campaigns, newest last.
campaigns:
	@curl -fsS -u any:$${MAILCHIMP_API_KEY:-fake-key-us1} "http://localhost:$${MAILCHIMP_FAKE_PORT:-8090}/3.0/campaigns" | python3 -m json.tool
