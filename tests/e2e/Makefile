# Cross-service end-to-end tests. Thin wrapper over ../../te-deployment,
# with the same target names as te-deployment/Makefile.
#   make e2e        against the running stack (starts what's missing, leaves it up)
#   make test-e2e   e2e, then stop the stack if it passed

DEPLOY := ../../te-deployment

.PHONY: e2e test-e2e

e2e:
	$(MAKE) -C $(DEPLOY) e2e

test-e2e:
	$(MAKE) -C $(DEPLOY) test-e2e
