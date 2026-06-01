# airfield-wx — common developer commands.
#
# Usage:
#   make install     first-time setup: create venv, install server in editable mode
#   make dev         run uvicorn with --reload against ./server (port 8005)
#   make serve       run uvicorn without --reload (closer to production)
#   make test        run the server pytest suite
#   make lint        run ruff against the server package
#   make typecheck   run mypy against the server package
#   make check       lint + typecheck + test
#   make widget      run the new tray widget with the SYSTEM python (needs gi)
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

.PHONY: help install dev serve test lint typecheck check widget clean distclean

help:
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z_-]+:.*## /{printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Create the server venv and install in editable mode with dev extras
	$(PYTHON) -m venv $(VENV)
	$(VPY) -m pip install --upgrade pip
	$(VPY) -m pip install -e "./server[dev]"

dev: ## uvicorn --reload (port 8005, listens on all interfaces)
	cd server && ../$(VENV)/bin/uvicorn weather_server.main:app --reload --host $(HOST) --port $(PORT)

serve: ## uvicorn without --reload
	cd server && ../$(VENV)/bin/uvicorn weather_server.main:app --host $(HOST) --port $(PORT)

test: ## pytest in the server package
	cd server && ../$(VENV)/bin/pytest -q

lint: ## ruff check on the server package
	cd server && ../$(VENV)/bin/ruff check weather_server tests

typecheck: ## mypy against the server package
	cd server && ../$(VENV)/bin/mypy weather_server

check: lint typecheck test ## All gates: lint + typecheck + test

widget: ## Run the new tray widget (uses system python for gi)
	$(SYSPY) widget/weather_tray.py

clean: ## Remove pycache + tool caches (preserves venv and db)
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf server/.pytest_cache server/.ruff_cache server/.mypy_cache

distclean: clean ## clean + remove the venv and weather.db
	rm -rf $(VENV) server/weather.db server/weather.db-wal server/weather.db-shm
