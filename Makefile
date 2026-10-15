# trends-engine: run the stack from the repo root without -C te-deployment.
# Every target is forwarded, as one call, to te-deployment/Makefile, so
#   make up | make up v2 | make down | make reset | make logs-poller | make test-e2e ...
# behave exactly as `make -C te-deployment <same words>`. `make` alone shows the help.
# Variables pass through too (make up FIXTURE=v2, CONNECTOR_BATCH_SIZE=1000 make up v2).

DEPLOY := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))te-deployment
GOALS  := $(or $(MAKECMDGOALS),help)

.PHONY: $(GOALS)

# The first goal makes the one forwarded call with all of them (so "up v2" stays together);
# the rest do nothing here.
$(firstword $(GOALS)):
	@$(MAKE) --no-print-directory -C $(DEPLOY) $(GOALS)

$(filter-out $(firstword $(GOALS)),$(GOALS)):
	@:
