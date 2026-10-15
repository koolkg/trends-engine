# te-infra: thin wrapper. Postgres, Redis, RabbitMQ and their one-shot init
# containers (postgres-migrate applies postgres/init/002+, rabbitmq-init
# imports rabbitmq/definitions.json) are defined in
# ../te-deployment/docker-compose.yml.

DEPLOY := ../te-deployment

.PHONY: start stop test-unit test-integration migrate

start:
	$(MAKE) -C $(DEPLOY) up-te-infra

stop:
	$(MAKE) -C $(DEPLOY) down-te-infra

# Migrations and the RabbitMQ definitions are re-applied every time the
# init containers run, which `start` does.
migrate: start

# The fake Mailchimp API's tests (mailchimp-fake/tests), in its container.
test-unit:
	$(MAKE) -C $(DEPLOY) test-unit-te-infra

test-integration:
	$(MAKE) -C $(DEPLOY) test-integration-te-infra
