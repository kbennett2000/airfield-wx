# Design — the surviving docs

Two docs remain from the rebuild's design phase. They're the ones that still answer questions the running code doesn't.

| File | What it is |
|---|---|
| [`01-findings.md`](01-findings.md) | **Decisions log.** Original code review of the legacy codebase, plus the record of why each major rebuild choice was made (SQLite over MariaDB, vanilla JS over React, dropping `/setOffset`, the pressure-quadruple resolution to BUG-21, no forecasting, etc.). When in doubt about whether to change a settled decision, this is the place to look first. |
| [`02-api-design.md`](02-api-design.md) | **API contract narrative.** The provenance taxonomy (RAW / CALIBRATED / D-READING / D-LOCATION / D-TIME / META), the auto-bucket heuristic and reasoning, the rule about disambiguating physical quantities by field name. The Pydantic models in [`server/weather_server/schemas.py`](../../server/weather_server/schemas.py) are the runtime contract; this doc has the why-it's-shaped-this-way. |

## Where the rest went

The original design phase produced seven docs. After the rebuild shipped, four of them were redundant with the running code and got deleted:

- `03-schema.md` → the schema is in [`server/weather_server/db.py`](../../server/weather_server/db.py).
- `04-server-architecture.md` → the architecture is the code under [`server/`](../../server/).
- `05-clients-scope.md` → the clients are the code under [`dashboard/`](../../dashboard/) and [`widget/`](../../widget/).
- `06-dashboard-mockup.html` → the live dashboard at `/dashboard/` is the visual reference now.

If you want them, they're in git history. The two kept here are the ones whose context isn't captured anywhere else.
