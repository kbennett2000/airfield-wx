# airfield-wx — public demo

A static, clickable demo of the dashboard so anyone can experience airfield-wx without building or
installing anything. It is deliberately, loudly **fake**.

> **DEMO — SIMULATED DATA · NOT A REAL STATION.** Every number here is frozen synthetic data for a
> fictional field ("DEMO / Example Field"). It is not live, not measured, and — like the product itself
> — **UNOFFICIAL, not for flight planning.**

## Why it's safe

- **Static snapshot, no backend.** The real app is a stateful FastAPI server (SQLite + background
  loops); the demo is just the dashboard's static files plus frozen JSON. There is **no server, no
  database, no background task** on the public box.
- **No live external calls.** The METAR shown is a hand-built synthetic sample frozen into JSON — the
  public deployment never contacts aviationweather.gov or anything else. It cannot decay or leak.
- **Fictional field.** The airport block is a *manual* config (`ident="DEMO"`, `source="manual"`) — no
  real airport's identity is ever presented as live. The snapshot generator asserts this.
- **The shipping dashboard is untouched.** The demo loads one extra script (`demo.js`) that points the
  dashboard's data fetches at the frozen JSON. The real server never loads it, so a real station still
  shows live data.

## How it works

```
demo/
  generate_snapshots.py   tooling: runs the REAL app against a fictional field + synthetic METAR,
                          freezes /current (vfr, mvfr, offline), /branding, /history → snapshots/
  snapshots/              the frozen synthetic JSON (committed — the "cannot rot" guarantee)
  demo.js                 fetch shim: maps /api/v1/* → ./snapshots/...; adds the scenario switcher
  demo.css                the DEMO banner + switcher styling
  build.py                assembles demo/public/: copies dashboard/ verbatim, derives index.html
                          (injects the DEMO banner + demo.js), drops in demo.css + snapshots
  vercel.json             static hosting config (cleanUrls)
  public/                 the assembled deploy artifact (gitignored; rebuilt on demand)
```

Three scenarios, switchable in-page (and via URL hash `#vfr` / `#mvfr` / `#offline`):

- **Clear (VFR)** — a lit runway solution and a green VFR METAR panel.
- **Marginal (MVFR)** — a broken ceiling + reduced visibility → blue MVFR.
- **Feed offline** — no external data → the violet METAR panel dims while the cyan local panels stay
  bright (airfield-wx's offline-first behavior).

## Build & preview locally

```bash
make demo-snapshots   # (re)generate the frozen synthetic JSON from the real app
make demo-build       # assemble demo/public/
make demo-serve       # serve it at http://localhost:8090/
```

## Deploy / redeploy / take down (Vercel)

Deployed as a **static** project (framework preset *Other*, output = `demo/public/`, no build command, no
env vars). To refresh after a UI or data change: re-run `make demo-snapshots && make demo-build`, then
redeploy `demo/public/`.

**Take it down:** Vercel dashboard → the `airfield-wx-demo` project → **Settings → Delete Project**
(or `vercel remove airfield-wx-demo`). It's a standing public artifact; remove it whenever you like.
