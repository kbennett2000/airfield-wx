"""Shared pytest fixtures.

The `client` fixture spins up the FastAPI app with a temp DB, the
example fixture sensor payloads, and the repo's branding.toml.example
as the branding source. Tests across multiple files reuse this rather
than duplicating the TOML template.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVER_ROOT = Path(__file__).parent.parent
FIXTURE_SRC = SERVER_ROOT / "fixtures"
REPO_ROOT = SERVER_ROOT.parent
BRANDING_EXAMPLE = REPO_ROOT / "branding.toml.example"

TOML_TEMPLATE = """
[server]
host = "127.0.0.1"
port = 8005
db_path = "{db_path}"
branding_path = "{branding_path}"

[logger]
interval_seconds = 60
http_timeout_seconds = 10

[cache]
ttl_seconds = 5

[development]
fixture_dir = "{fixture_dir}"

[[sensors]]
id = "outdoor"
role = "outdoor"
ip = "192.168.1.60"
has_gps = true
has_light = true
online_threshold_seconds = 120
temp_offset_c = -0.5
fallback_altitude_m = 1609.3
"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(FIXTURE_SRC, fixture_dir)

    db_path = tmp_path / "weather.db"
    cfg = tmp_path / "weather.toml"
    cfg.write_text(
        TOML_TEMPLATE.format(
            db_path=str(db_path),
            fixture_dir=str(fixture_dir),
            branding_path=str(BRANDING_EXAMPLE),
        )
    )

    monkeypatch.setenv("WEATHER_CONFIG", str(cfg))

    # Re-import to pick up the env var via lifespan.
    from weather_server.main import create_app

    app = create_app()
    with TestClient(app) as tc:
        yield tc
