#!/usr/bin/env bash
#
# Refresh the vendored Chart.js + Google Fonts bundle.
#
# The dashboard ships with snapshots of Chart.js and the three Google
# Fonts families it uses, so the page works on a LAN with no internet
# access (per the README's "nothing leaves your LAN" claim). This
# script re-downloads them — run it when you need to bump Chart.js
# or pick up upstream font updates.
#
# Usage:
#   ./dashboard/vendor/refresh.sh
#
# Requires: curl, python3. No package install needed.

set -euo pipefail

CHART_VERSION="${CHART_VERSION:-4.4.0}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FONTS_URL="https://fonts.googleapis.com/css2?family=Saira+Stencil+One&family=JetBrains+Mono:wght@300;400;500;700&family=IBM+Plex+Sans+Condensed:wght@300;400;500;600;700&display=swap"

# Mimic a real browser so Google Fonts hands us WOFF2 instead of TTF.
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

echo "==> Chart.js $CHART_VERSION"
curl -sSf --max-time 30 -o "$HERE/chart.umd.min.js" \
    "https://cdn.jsdelivr.net/npm/chart.js@${CHART_VERSION}/dist/chart.umd.min.js"

echo "==> Google Fonts CSS"
curl -sSf --max-time 30 -A "$UA" "$FONTS_URL" > "$HERE/.fonts.css.remote"

echo "==> WOFF2 files + rewritten CSS"
python3 - "$HERE" << 'PY'
"""Download each WOFF2 from the remote CSS and rewrite the CSS to point
at the local file. File names are <family>-<weight>-<style>-<rangehash>.
"""
import re, sys, hashlib, urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HERE = Path(sys.argv[1])
SRC = HERE / ".fonts.css.remote"
DST_CSS = HERE / "fonts.css"
DST_FONT_DIR = HERE / "fonts"
DST_FONT_DIR.mkdir(parents=True, exist_ok=True)

css = SRC.read_text()
blocks = re.findall(r"@font-face\s*\{([^}]*)\}", css)
remap: dict[str, str] = {}
for body in blocks:
    fam = re.search(r"font-family:\s*'([^']+)'", body)
    weight = re.search(r"font-weight:\s*(\d+)", body)
    style = re.search(r"font-style:\s*(\w+)", body)
    url = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", body)
    rng = re.search(r"unicode-range:\s*([^;]+);", body)
    if not (fam and weight and style and url):
        continue
    tag = hashlib.sha1((rng.group(1).strip() if rng else "").encode()).hexdigest()[:6]
    fname = f"{fam.group(1).replace(' ', '-')}-{weight.group(1)}-{style.group(1)}-{tag}.woff2"
    remap[url.group(1)] = fname

for remote, fname in remap.items():
    dst = DST_FONT_DIR / fname
    if dst.exists():
        continue
    req = urllib.request.Request(remote, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r, dst.open("wb") as f:
        f.write(r.read())

def rewrite(m: re.Match) -> str:
    return f"url(fonts/{remap[m.group(1)]})" if m.group(1) in remap else m.group(0)

new_css = re.sub(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", rewrite, css)
DST_CSS.write_text(new_css)
print(f"  {len(remap)} woff2 files, fonts.css rewritten")
PY

rm -f "$HERE/.fonts.css.remote"

echo ""
echo "Done. Diff against last commit to see what changed:"
echo "  git status -- dashboard/vendor"
echo "  git diff -- dashboard/vendor/fonts.css"
