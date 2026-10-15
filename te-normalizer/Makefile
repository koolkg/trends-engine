# te-normalizer: thin wrapper. Every container is defined in
# ../te-deployment/docker-compose.yml and every target here delegates to
# ../te-deployment/Makefile (make -C te-deployment help lists everything).

DEPLOY  := ../te-deployment
COMPOSE := docker-compose -p trends-engine -f $(DEPLOY)/docker-compose.yml

.PHONY: start stop restart logs test-unit test-integration eval

start:
	$(MAKE) -C $(DEPLOY) up-te-normalizer

stop:
	$(MAKE) -C $(DEPLOY) down-te-normalizer

restart:
	$(MAKE) -C $(DEPLOY) restart-te-normalizer

logs:
	$(MAKE) -C $(DEPLOY) logs-te-normalizer

test-unit:
	$(MAKE) -C $(DEPLOY) test-unit-te-normalizer

test-integration:
	$(MAKE) -C $(DEPLOY) test-integration-te-normalizer

# Classifier precision/recall/category accuracy against te-connector's fixture labels.
eval:
	$(COMPOSE) build te-normalizer
	$(COMPOSE) run --rm --no-deps -v "$(abspath ../te-connector/fixtures)":/fixtures:ro te-normalizer \
		python scripts/eval_classifier.py /fixtures/raw_content.jsonl /fixtures/raw_content.labels.jsonl --sweep $(ARGS)
