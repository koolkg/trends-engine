# te-ui: thin wrapper. The container (nginx + the built app) and its test image
# are defined in ../te-deployment/docker-compose.yml; every target delegates to
# ../te-deployment/Makefile. Node and node_modules only ever live in the image.

DEPLOY := ../te-deployment

.PHONY: start stop restart logs test-unit test-integration open

start:
	$(MAKE) -C $(DEPLOY) up-te-ui

stop:
	$(MAKE) -C $(DEPLOY) down-te-ui

restart:
	$(MAKE) -C $(DEPLOY) restart-te-ui

logs:
	$(MAKE) -C $(DEPLOY) logs-te-ui

# Type-check + Jest / React Testing Library, in the build-stage image.
test-unit:
	$(MAKE) -C $(DEPLOY) test-unit-te-ui

# The real nginx container: app served, SPA fallback, /v1 proxied (tests/e2e/test_6_ui.py).
test-integration:
	$(MAKE) -C $(DEPLOY) test-integration-te-ui

# Open the dashboard in your browser (macOS).
open:
	open "http://localhost:$${UI_HOST_PORT:-3000}"
