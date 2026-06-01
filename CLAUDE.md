# CLAUDE.md

Read this before doing anything. It orients Claude Code to **airfield-wx**.

## Project overview

airfield-wx is a self-hosted, GPS-aware weather station any pilot can build and run at their own
airstrip. ESP32 sensors feed a FastAPI server on the home LAN, which serves a cockpit-style dashboard
and a read-only HTTP API. It is built on a copy of the `weather-station-public` codebase (an aviation
variant, **not** a fork). **It is UNOFFICIAL — never a substitute for an official preflight briefing.**

## Tech stack

- **Python 3.11+** (stdlib `tomllib`).
- **FastAPI + uvicorn**, **Pydantic v2** (strict models).
- **SQLite** via stdlib `sqlite3` (WAL mode), single file.
- **requests** (sensor polling + external providers); **timezonefinder** (offline timezone).
- **Bundled offline datasets:** OurAirports CSVs (airports + runways); WMM2025 coefficients for magnetic
  declination, read via a WMM library (e.g. GeographicLib binding or a pure-Python WMM port).
- **Dashboard:** vanilla JS + HTML + SVG, Chart.js (vendored), self-hosted fonts. **No React / Babel /
  Tailwind / framework.**
- **Widget (optional):** GTK3 + AppIndicator3 (Python `gi`).
- **ESP32 firmware:** Arduino / FreeRTOS. Outdoor unit = BME280 + GPS + anemometer (no light sensor).
- **Tooling:** pytest, pytest-asyncio, httpx, ruff, mypy (strict).
- **Target host:** Raspberry Pi Zero 2 W or any Ubuntu/Debian box.

## Architecture

- Single FastAPI process; the app lives in `server/weather_server/`.
- **One source of truth:** a read-only, versioned HTTP API under `/api/v1/`. Every client (dashboard,
  tray widget, any future device) polls the same endpoints. No per-client math.
- **DB stores RAW sensor readings only** (`outdoor_readings`, one table, WAL). Every conversion / derived
  value is computed at **read time** in `derivations/`. Never persist a derived value.
- Endpoints in `routes/`; response shapes composed in `responses.py`; runtime contract in `schemas.py`.
- Concurrency: outdoor logger task; optional external-feed task (no-op when disabled); on-demand
  poll of any non-outdoor station behind a TTL cache.
- **Provenance taxonomy** tags every field: RAW / CALIBRATED / D-READING / D-LOCATION / D-TIME / META /
  EXTERNAL / D-HISTORY.
- **Offline-first:** the server runs with no internet; the `external` block is the *only* thing that
  differs online vs offline.
- Config in TOML (`weather.toml`); branding/copy in `branding.toml` (config-driven slots).
- Sensors: ESP32 `/data` → `wire_format.py` adapter → internal payload (also the fixture/DB shape).
- Aviation additions: altimeter setting (`derivations/readings.py`); location intelligence
  (`derivations/airport.py` + bundled OurAirports/WMM); runway components; METAR provider
  (`external/providers.py`); a new `GET /api/v1/airport` endpoint; the EFIS dashboard reskin.

## Conventions

- **Read `docs/design/` and `docs/adr/` before starting.** They state intent; the code is the
  implementation. Both matter. Do **not** edit design docs — raise a question if you find a defect.
- **The locked decisions in `docs/design/01-aviation-design.md` are not to be re-litigated.** If you
  think one is wrong, surface it as a question; never silently override.
- Derived values are computed server-side at read time; the DB is raw-only.
- **Disambiguate physical quantities by field name** (`pressure_station_*` vs `pressure_sealevel_*` vs
  `altimeter_setting_*`). Never offer one ambiguous field.
- Pydantic models are strict (`extra="forbid"`, frozen); every endpoint response is a model.
- Derivations are **pure functions**; missing inputs cascade to `null`, never raise.
- **No hardcoded location, airport, or station** anywhere in code. Everything location-dependent flows
  from GPS + config.
- **Branding text lives in `branding.toml`** as visible placeholder slots — never invent branding, and
  no "Jones" references survive.
- Code identifiers stay professional and straightforward. UI/README copy may have personality; variable,
  function, and module names do not.
- File layout mirrors the base: `server/weather_server/{routes,derivations,external}/`, `dashboard/`,
  `widget/`, `sketches/`, `tests/`, `docs/`.
- Imports: stdlib first, then third-party, then local (`from .x import y`); `from __future__ import
  annotations` at the top of every module.
- Units are configurable; knots and feet stay fixed. **Flight category comes only from a METAR.**

## Out of scope for v1

- Forecasting / TAF (observations only).
- Ceiling measurement (no ceilometer; ceiling is METAR-sourced and attributed).
- Auth / remote access (LAN-only; remote is a reverse-proxy concern).
- Write endpoints / sensor calibration over HTTP (config + restart).
- Multi-airport (one station, one field).
- UI copy localization (units configurable; copy stays English).
- Regulatory overlay enabled by default (twilight data present; legal interpretation off).
- Light sensor (TSL2591 removed; `has_light = false`).

## Git Workflow

After any code change is complete and verified (tests pass / lint clean /
feature works), do the following without being asked:

1. `git add -A` to stage all changes
2. Commit with a concise conventional-commit message
   (e.g. `feat: add user auth middleware`, `fix: handle empty cart edge case`,
   `refactor: extract validation into shared module`, `docs: update README`)
3. `git push` to push to origin/main

Commit at logical checkpoints — a complete feature, a bug fix, a refactor —
not after every individual file edit. If a task spans multiple commits,
make each commit independently meaningful and atomic.

If `git push` fails (auth, conflict, network), surface the full error to the
user immediately. Do not retry silently or attempt destructive resolutions
(no `--force`, no resetting branches).

Never commit secrets, API keys, .env files, or anything matching .gitignore.

## Engineering Principles

### Tests are required, not optional
- Every new feature, bug fix, or non-trivial change ships with tests.
- For new functionality, prefer test-first: write the test from the spec,
  then implement until it passes.
- A task is not "done" until the relevant tests pass. Do not report completion
  with failing or skipped tests.
- When fixing a bug, first write a test that reproduces the bug (and fails),
  then fix it. This prevents regressions.
- Keep the test suite fast. If a test is slow, isolate it (mark as integration
  or e2e) so the default `test` command stays under 10 seconds for unit tests.

### Tight feedback loops
- Use strict typing everywhere (TypeScript strict mode / Pydantic / Zod —
  whatever the stack supports). Type errors should surface immediately.
- Run lint and typecheck before declaring a task complete.
- Add structured logging at module boundaries from day one. When something
  breaks, logs should narrow the cause in seconds, not minutes.
- If a change requires manual verification (UI, integrations), state exactly
  what to check and how — don't leave it implicit.

### Spec before code for non-trivial work
- For any task touching 3+ files, introducing a new module, or changing a
  contract between components: produce a spec FIRST in plan mode. Do not
  start editing until the user has approved the plan.
- For significant architectural decisions, write a short ADR (Architecture
  Decision Record) in `/docs/adr/` capturing: context, options considered,
  decision, consequences. Reference the ADR in commit messages.
- Read `/docs/` and `/specs/` (if they exist) before starting work. Those
  files describe intent; the code describes implementation. Both matter.

### Taste and restraint
- Prefer the simplest solution that solves the problem. Resist adding
  abstraction, config options, or framework features that aren't justified
  by an actual requirement.
- If a diff is getting large, stop and ask whether the task should be
  decomposed into smaller commits.
- Reuse existing patterns in the codebase before inventing new ones.
