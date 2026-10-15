# trends-engine: the whole stack from one place.
# Run from the repo root as `make -C te-deployment <target>`, or cd here first.
#
# Every container is defined in docker-compose.yml next to this file (one
# compose project, one network; see that file). Each te-*/Makefile also points
# at it, so `make start` / `make test-unit` inside a service directory works too.

# Absolute path to this directory, so the targets work whichever directory
# make is run from (make -C te-deployment, cd here, or make -f te-deployment/Makefile).
HERE    := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
COMPOSE := docker-compose -p trends-engine -f $(HERE)docker-compose.yml

INFRA    := postgres redis rabbitmq postgres-migrate rabbitmq-init mailchimp-fake
PY_SERVICES := te-connector te-normalizer te-detector te-cluster te-aggregator te-api
SERVICES    := $(PY_SERVICES) te-ui
UNIT_TESTS := python -m unittest discover -s tests/unit -t . -v
IT_TESTS   := python -m unittest discover -s tests/integration -t . -v

# Which fixture te-connector replays: make up v2 (default v1; FIXTURE=v2 works too).
#   v1: fixtures/raw_content.jsonl, ~1.4k posts on one day, 5 posts / 30 s, loops forever.
#   v2: fixtures/raw_content_v2.jsonl, ~41k posts over 5 days, 500 posts / 30 s (~40 min), one pass.
# Variables you set yourself (e.g. CONNECTOR_BATCH_SIZE=1000) still win.
# "v1"/"v2" given as words on the command line (make up v2) pick the fixture;
# the matching do-nothing targets below keep make from treating them as goals to build.
ifneq ($(filter-out v1 v2,$(filter v%,$(MAKECMDGOALS))),)
$(error unknown fixture '$(filter-out v1 v2,$(filter v%,$(MAKECMDGOALS)))': use v1 or v2)
else ifneq ($(filter v2,$(MAKECMDGOALS)),)
FIXTURE := v2
else ifneq ($(filter v1,$(MAKECMDGOALS)),)
FIXTURE := v1
endif
FIXTURE ?= v1
ifeq ($(FIXTURE),v2)
export CONNECTOR_FIXTURE_PATH ?= fixtures/raw_content_v2.jsonl
export CONNECTOR_BATCH_SIZE   ?= 500
export CONNECTOR_LOOP_FIXTURE ?= false
else ifneq ($(FIXTURE),v1)
$(error FIXTURE must be v1 or v2, got '$(FIXTURE)')
endif

# make logs [name ...]: follow the logs of one or more services, by short name
# (several at once interleave; none or "all" = everything). Full names work too.
LOGS_poller     := te-connector
LOGS_connector  := te-connector
LOGS_normalizer := te-normalizer
LOGS_detector   := te-detector
LOGS_cluster    := te-cluster
LOGS_aggregator := te-aggregator
LOGS_api        := te-api
LOGS_ui         := te-ui
LOGS_infra      := postgres redis rabbitmq mailchimp-fake
LOGS_postgres   := postgres
LOGS_redis      := redis
LOGS_rabbitmq   := rabbitmq
LOGS_mailchimp  := mailchimp-fake
$(foreach s,$(SERVICES),$(eval LOGS_$(s) := $(s)))
LOGS_NAMES := all poller connector normalizer detector cluster aggregator api ui infra postgres redis rabbitmq mailchimp $(SERVICES)
TAIL ?= 100
ifneq ($(filter logs,$(MAKECMDGOALS)),)
LOGS_WORDS := $(filter-out logs,$(MAKECMDGOALS))
ifneq ($(filter-out $(LOGS_NAMES),$(LOGS_WORDS)),)
$(error make logs: unknown name '$(filter-out $(LOGS_NAMES),$(LOGS_WORDS))'. Use: $(LOGS_NAMES))
endif
# "all" (or no names) = no service list = compose follows every container
LOGS_SERVICES := $(if $(filter all,$(LOGS_WORDS)),,$(sort $(foreach w,$(LOGS_WORDS),$(LOGS_$(w)))))
.PHONY: $(LOGS_WORDS)
$(LOGS_WORDS):
	@:
endif

.PHONY: v1 v2 fixture-ready help up down restart status logs build reset fixtures fixtures-v2 \
        test test-unit test-integration test-e2e e2e \
        $(addprefix up-,te-infra $(SERVICES)) $(addprefix down-,te-infra $(SERVICES)) \
        $(addprefix restart-,te-infra $(SERVICES)) $(addprefix logs-,te-infra $(SERVICES)) \
        $(addprefix test-unit-,te-infra $(SERVICES)) $(addprefix test-integration-,te-infra $(SERVICES))

# Fixture switches for make up / restart / up-te-connector (see FIXTURE above).
v1 v2:
	@:

help:
	@echo "Stack"
	@echo "  make up                      build if needed and start everything (infra first)"
	@echo "  make up v2                   same, but te-connector replays the 5-day v2 fixture (make reset first)"
	@echo "  make down                    stop and remove all containers (data is kept)"
	@echo "  make restart                 down + up"
	@echo "  make status                  what's running"
	@echo "  make logs [name ...]         follow logs, interleaved: e.g. make logs poller aggregator"
	@echo "                               names: poller normalizer detector cluster aggregator api ui"
	@echo "                               infra (or postgres redis rabbitmq mailchimp), all (default)"
	@echo "                               TAIL=500 for more history (default 100)"
	@echo "  make reset                   down AND wipe all data (Postgres, RabbitMQ, Redis)"
	@echo "Fixtures (te-connector's replay data, generated in a container)"
	@echo "  make fixtures-v2             regenerate the v2 fixture (the 5 days up to now)"
	@echo "  make fixtures                regenerate the v1 fixture, then run te-connector's integration tests"
	@echo "One service ($(SERVICES), or te-infra)"
	@echo "  make up-<svc> / down-<svc> / restart-<svc> / logs-<svc>"
	@echo "Tests (all in containers)"
	@echo "  make test-unit               every service's unit tests (no infra needed)"
	@echo "  make test-integration        te-infra smoke test + every service's integration tests"
	@echo "  make e2e                     end-to-end suite on the current data (starts the stack if needed, leaves it running)"
	@echo "  make test-e2e                reset (wipes all data), e2e, then stop the stack if it passed"
	@echo "  make test                    all of the above"
	@echo "  make test-unit-<svc> / test-integration-<svc>"

## --- stack ------------------------------------------------------------------

# v2's data files aren't committed (large, and dated from the day they're made):
# generate them on first use.
V2_FIXTURE := $(HERE)../te-connector/fixtures/raw_content_v2.jsonl
fixture-ready:
ifeq ($(FIXTURE),v2)
	@test -f "$(V2_FIXTURE)" || { echo "v2 fixture not found: generating it first (make fixtures-v2)"; \
		$(MAKE) --no-print-directory fixtures-v2; }
endif

up: fixture-ready
	@echo "te-connector fixture: $(FIXTURE) ($${CONNECTOR_FIXTURE_PATH:-fixtures/raw_content.jsonl})"
	$(COMPOSE) up -d --build
	@echo "te-ui: http://localhost:$${UI_HOST_PORT:-3000}   te-api: http://localhost:$${API_HOST_PORT:-8080}/v1/trends   RabbitMQ UI: http://localhost:15672"

down:
	$(COMPOSE) down

restart: down up

status:
	$(COMPOSE) ps -a

logs:
	$(COMPOSE) logs -f --tail $(TAIL) $(LOGS_SERVICES)

build:
	$(COMPOSE) build

reset:
	$(COMPOSE) down -v

## --- fixtures (te-connector's replay data) ------------------------------------
## Run te-connector's own targets, so `make fixtures-v2` works from here and from
## the repo root. ARGS passes through, e.g. make fixtures-v2 ARGS="--days 3".

fixtures fixtures-v2:
	$(MAKE) --no-print-directory -C $(HERE)../te-connector $@

## --- one service --------------------------------------------------------------
## Static pattern rules ("$(addprefix ...): x-%:"): a plain "x-%:" pattern is
## never applied to targets listed in .PHONY, so make would do nothing.

up-te-infra:
	$(COMPOSE) up -d $(INFRA)

down-te-infra:
	$(COMPOSE) stop $(INFRA)

restart-te-infra:
	$(COMPOSE) up -d --force-recreate $(INFRA)

logs-te-infra:
	$(COMPOSE) logs -f --tail 100 postgres redis rabbitmq mailchimp-fake

$(addprefix up-,$(SERVICES)): up-%: fixture-ready
	$(COMPOSE) up -d --build $*

$(addprefix down-,$(SERVICES)): down-%:
	$(COMPOSE) stop $*

$(addprefix restart-,$(SERVICES)): restart-%: fixture-ready
	$(COMPOSE) up -d --build --force-recreate $*

$(addprefix logs-,$(SERVICES)): logs-%:
	$(COMPOSE) logs -f --tail 100 $*

## --- tests ----------------------------------------------------------------------
## `run --rm` starts a one-off container from the service's image with a
## different command. Unit tests use --no-deps (no infra); integration tests
## let compose start whatever infra the service depends on.

test-unit: test-unit-te-infra $(addprefix test-unit-,$(SERVICES))

# te-infra's only code with unit tests: the fake Mailchimp API.
test-unit-te-infra:
	$(COMPOSE) build mailchimp-fake
	$(COMPOSE) run --rm --no-deps mailchimp-fake python -m unittest discover -s tests -t . -v

$(addprefix test-unit-,$(PY_SERVICES)): test-unit-%:
	$(COMPOSE) build $*
	$(COMPOSE) run --rm --no-deps $* $(UNIT_TESTS)

# te-ui: type-check + Jest/RTL in its build-stage image (node + node_modules inside it).
test-unit-te-ui:
	$(COMPOSE) --profile test build te-ui-test
	$(COMPOSE) --profile test run --rm te-ui-test

test-integration: test-integration-te-infra $(addprefix test-integration-,$(SERVICES))

test-integration-te-infra:
	$(COMPOSE) --profile test build te-infra-smoke
	$(COMPOSE) --profile test run --rm te-infra-smoke

$(addprefix test-integration-,$(PY_SERVICES)): test-integration-%:
	$(COMPOSE) build $*
	$(COMPOSE) run --rm $* $(IT_TESTS)

# te-ui: the real nginx container (SPA routing, /v1 proxying) checked from the e2e
# image by tests/e2e/test_6_ui.py. Starts te-ui and what it needs (te-api, infra).
test-integration-te-ui:
	$(COMPOSE) up -d --build --wait te-ui
	$(COMPOSE) --profile e2e build e2e
	$(COMPOSE) --profile e2e run --rm --no-deps e2e python -m unittest -v test_6_ui

# The e2e suite replays the fixture itself at full speed, so te-connector is
# stopped first: its every-30s poll-completed signals would trigger extra
# aggregation cycles and break the debounce scenario.
e2e:
	-$(COMPOSE) stop te-connector
	$(COMPOSE) --profile e2e build e2e
	$(COMPOSE) --profile e2e run --rm e2e

# test-e2e always starts from clean data (the reset wipes Postgres, RabbitMQ and
# Redis): data from another fixture, e.g. te-connector's v2, would break it.
test-e2e:
	@echo "test-e2e: wiping all data first (make reset) so the run starts clean"
	@$(MAKE) --no-print-directory reset
	@$(MAKE) --no-print-directory e2e || { \
		echo; echo "e2e failed: the stack is left running so you can inspect it (make status / logs-<svc>)."; \
		echo "Re-run with: make -C te-deployment e2e    Stop with: make -C te-deployment down"; exit 1; }
	@$(MAKE) --no-print-directory down

test: test-unit test-integration test-e2e
