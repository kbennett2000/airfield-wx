# airfield-wx — common developer commands.
#
# Usage:
#   make install     first-time setup: create venv, install server in editable mode
#   make dev         run uvicorn with --reload against ./server (port 8005)
#   make serve       run uvicorn without --reload (closer to production)
#   make test        run the fast unit suite (integration tests deselected)
#   make test-integration  run only the integration tests (real HTTP/timing)
#   make test-all    run the full suite (unit + integration)
#   make lint        run ruff against the server package
#   make typecheck   run mypy against the server package
#   make check       lint + typecheck + test
#   make widget      run the new tray widget with the SYSTEM python (needs gi)
#   make data-refresh re-download the bundled OurAirports CSVs
#   make clean       remove pycache, .pytest_cache, ruff/mypy caches
#   make distclean   clean + nuke the server venv and weather.db

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PYTHON ?= python3
VENV   := server/.venv
VPY    := $(VENV)/bin/python
HOST   ?= 0.0.0.0
# PORT overrides what's in weather.toml only for `make dev` / `make serve`,
# where we pass --port to uvicorn explicitly. The deployed systemd unit
# reads weather.toml — change it there for a permanent override.
PORT   ?= 8005

# System python — the widget depends on the apt-installed python3-gi, which
# the server venv doesn't expose.
SYSPY := /usr/bin/python3

.PHONY: help install config dev serve test test-integration test-all lint typecheck check widget data-refresh screenshots demo-snapshots demo-build demo-serve clean distclean

DEMO_PORT ?= 8090

OURAIRPORTS_DIR := server/weather_server/data/ourairports
OURAIRPORTS_URL := https://davidmegginson.github.io/ourairports-data

help:
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z_-]+:.*## /{printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Create the server venv and install in editable mode with dev extras
	$(PYTHON) -m venv $(VENV)
	$(VPY) -m pip install --upgrade pip
	$(VPY) -m pip install -e "./server[dev]"

config: ## Seed server/weather.toml from the example if it doesn't exist yet
	@if [ ! -f server/weather.toml ]; then \
		cp server/weather.toml.example server/weather.toml; \
		echo "seeded server/weather.toml from weather.toml.example (fixture/demo mode)"; \
	fi

dev: config ## uvicorn --reload (port 8005, listens on all interfaces)
	cd server && ../$(VENV)/bin/uvicorn weather_server.main:app --reload --host $(HOST) --port $(PORT)

serve: config ## uvicorn without --reload
	cd server && ../$(VENV)/bin/uvicorn weather_server.main:app --host $(HOST) --port $(PORT)

test: ## fast unit suite (integration tests deselected via addopts)
	cd server && ../$(VENV)/bin/pytest -q

test-integration: ## run only the integration tests (real HTTP + timing)
	cd server && ../$(VENV)/bin/pytest -q -m integration

test-all: ## run the full suite — unit + integration
	cd server && ../$(VENV)/bin/pytest -q -m "integration or not integration"

lint: ## ruff check on the server package
	cd server && ../$(VENV)/bin/ruff check weather_server tests

typecheck: ## mypy against the server package
	cd server && ../$(VENV)/bin/mypy weather_server

check: lint typecheck test ## All gates: lint + typecheck + test

widget: ## Run the new tray widget (uses system python for gi)
	$(SYSPY) widget/weather_tray.py

data-refresh: ## Re-download the bundled OurAirports CSVs (public domain)
	mkdir -p $(OURAIRPORTS_DIR)
	curl -fsS $(OURAIRPORTS_URL)/airports.csv -o $(OURAIRPORTS_DIR)/airports.csv
	curl -fsS $(OURAIRPORTS_URL)/runways.csv -o $(OURAIRPORTS_DIR)/runways.csv
	@echo "refreshed OurAirports data in $(OURAIRPORTS_DIR)"

screenshots: ## Regenerate the dashboard screenshots in docs/assets/screenshots/ (Playwright; tooling only, not part of `check`)
	$(VPY) -m pip install -e "./server[tooling]"
	# Install the bundled Chromium; if this OS has no bundled build, the script
	# falls back to a system Chrome/Chromium, so don't fail the target here.
	-$(VPY) -m playwright install chromium
	$(VPY) scripts/screenshots/capture.py

demo-snapshots: ## Regenerate the frozen synthetic demo snapshots in demo/snapshots/ (tooling)
	$(VPY) demo/generate_snapshots.py

demo-build: ## Assemble the static demo site into demo/public/ (rebuilds from dashboard/ + snapshots)
	$(VPY) demo/build.py

demo-serve: ## Serve the built demo locally for review (after demo-build)
	cd demo/public && ../../$(VENV)/bin/python -m http.server $(DEMO_PORT)

clean: ## Remove pycache + tool caches (preserves venv and db)
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf server/.pytest_cache server/.ruff_cache server/.mypy_cache

distclean: clean ## clean + remove the venv and weather.db
	rm -rf $(VENV) server/weather.db server/weather.db-wal server/weather.db-shm
