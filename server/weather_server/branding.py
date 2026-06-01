"""Branding config loader.

The dashboard has a handful of human-editable text slots ([BRANDING] in
the mockup). Those slots are filled from a TOML file at the repo root
(branding.toml). If branding.toml is missing, we fall back to the
checked-in branding.toml.example so the dashboard never breaks — the
.example file ships with placeholder text, not empty strings.

The loaded blob is cached in app.state on startup; there's no hot
reload. Edit the file and restart the server.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _empty_branding() -> dict[str, Any]:
    """Last-resort schema-shaped default if even the .example is missing.
    Every key the dashboard might read is present so the JS can index
    without optional-chaining gymnastics."""
    return {
        "header": {"tagline": "", "title": "", "subtitle": "", "system_mark": ""},
        "footer": {"text": "", "system_line": ""},
        "browser_title": {"text": "Weather Station"},
        "states": {
            "outdoor_offline": "",
            "loading": "",
        },
        "error": {"generic": ""},
        "taglines": {"rotating": []},
    }


def load_branding(branding_path: str | Path) -> dict[str, Any]:
    """Read the live branding.toml, or fall back to <path>.example, or
    fall back to a minimal empty schema. Always returns a dict shaped
    like the schema."""
    path = Path(branding_path)
    if path.is_file():
        return _read_toml(path, label="branding.toml")

    example_path = path.with_suffix(path.suffix + ".example")
    if example_path.is_file():
        log.info(
            "branding.toml not found at %s; falling back to %s",
            path, example_path,
        )
        return _read_toml(example_path, label="branding.toml.example")

    log.warning(
        "neither %s nor %s exists; using minimal empty branding",
        path, example_path,
    )
    return _empty_branding()


def _read_toml(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.error("failed to read %s (%s); using empty branding", label, exc)
        return _empty_branding()

    # Normalize: ensure every top-level section exists so the API
    # response is always shape-stable, even if the user partially
    # edited the file.
    defaults = _empty_branding()
    for section, default_value in defaults.items():
        if section not in data:
            data[section] = default_value
        elif isinstance(default_value, dict):
            for k, v in default_value.items():
                data[section].setdefault(k, v)
    log.info("loaded branding from %s", path)
    return data
