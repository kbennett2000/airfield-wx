#!/usr/bin/env python3
"""Assemble the static demo site into demo/public/ (the Vercel deploy artifact).

Copies the SHIPPING dashboard verbatim (so it never drifts from the real one),
derives a demo `index.html` from it by injecting the DEMO banner + the demo.js
shim, and drops in the demo stylesheet, the fetch shim, and the frozen
snapshots. The shipping dashboard/ files are never modified.

Run `make demo-snapshots` first (to (re)generate demo/snapshots/), then
`make demo-build`. demo/public/ is gitignored — it is rebuilt on demand.
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DASHBOARD = REPO / "dashboard"
DEMO = REPO / "demo"
SNAPSHOTS = DEMO / "snapshots"
OUT = DEMO / "public"

GH_URL = "https://github.com/kbennett2000/airfield-wx"

DEMO_BANNER_HTML = (
    '\n  <!-- ══ DEMO — injected by demo/build.py; not in the shipping dashboard ══ -->\n'
    '  <div class="demo-banner" role="alert">\n'
    '    <span class="demo-dot">▶</span> DEMO — SIMULATED DATA · NOT A REAL STATION\n'
    f'    <a href="{GH_URL}">what is this?</a>\n'
    '    <small>Frozen synthetic readings for a fictional field. Nothing here is live or measured. '
    'Not for flight planning.</small>\n'
    '  </div>\n'
)


def _derive_index(src_html: str) -> str:
    html = src_html

    # 1. Demo stylesheet, right after the shipping stylesheet link.
    marker = '<link rel="stylesheet" href="style.css">'
    assert marker in html, "could not find the style.css link to anchor demo.css"
    html = html.replace(marker, marker + '\n<link rel="stylesheet" href="demo.css">', 1)

    # 2. A clearer browser title.
    html = html.replace("<title>Weather Station</title>", "<title>airfield-wx — demo</title>", 1)

    # 3. The DEMO banner, immediately after the persistent UNOFFICIAL caution block.
    caution_close = (
        "    UNOFFICIAL &mdash; NOT FOR FLIGHT PLANNING\n"
        "    <small>Backyard sensor data + nearby reports. Not a substitute for an official "
        "preflight briefing or current METAR/TAF.</small>\n"
        "  </div>\n"
    )
    assert caution_close in html, "could not find the caution block to anchor the demo banner"
    html = html.replace(caution_close, caution_close + DEMO_BANNER_HTML, 1)

    # 4. The fetch shim, BEFORE app.js (defer preserves order → it patches fetch first).
    app_script = '<script src="app.js" defer></script>'
    assert app_script in html, "could not find the app.js script tag"
    html = html.replace(app_script, '<script src="demo.js" defer></script>\n' + app_script, 1)

    return html


def main() -> None:
    if not SNAPSHOTS.exists() or not (SNAPSHOTS / "vfr" / "current.json").exists():
        raise SystemExit("demo/snapshots not found — run `make demo-snapshots` first.")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # Shipping dashboard, copied verbatim.
    shutil.copy(DASHBOARD / "app.js", OUT / "app.js")
    shutil.copy(DASHBOARD / "style.css", OUT / "style.css")
    shutil.copytree(DASHBOARD / "vendor", OUT / "vendor")

    # Demo-only assets.
    shutil.copy(DEMO / "demo.js", OUT / "demo.js")
    shutil.copy(DEMO / "demo.css", OUT / "demo.css")
    shutil.copytree(SNAPSHOTS, OUT / "snapshots")
    if (DEMO / "vercel.json").exists():
        shutil.copy(DEMO / "vercel.json", OUT / "vercel.json")

    # Derived index.html.
    (OUT / "index.html").write_text(_derive_index((DASHBOARD / "index.html").read_text()))

    print(f"built demo site at {OUT.relative_to(REPO)}/")
    for p in sorted(OUT.iterdir()):
        print(f"  {p.name}{'/' if p.is_dir() else ''}")


if __name__ == "__main__":
    main()
