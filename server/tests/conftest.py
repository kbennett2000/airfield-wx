"""Shared pytest fixtures.

The `client` fixture spins up the FastAPI app with a temp DB, the
example fixture sensor payloads, and the repo's branding.toml.example
as the branding source. Tests across multiple files reuse this rather
than duplicating the TOML template.
"""

from __future__ import annotations

import shutil
import tempfile
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

# History logging is OFF by default (Cycle 13), so the default `client` is a
# stateless station: /current & friends work via a live cached poll of the
# fixture source, and /history + /summary return 404. Tests that need the log
# (history, summary, trends) use `logging_client`, which enables it.
LOGGING_TOML_TEMPLATE = TOML_TEMPLATE + "\n[logging]\nenabled = true\n"


def _spin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str) -> TestClient:
    # A unique workdir per call so a single test can use both `client` and
    # `logging_client` without colliding on the fixtures dir / db / config.
    workdir = Path(tempfile.mkdtemp(dir=tmp_path))
    fixture_dir = workdir / "fixtures"
    shutil.copytree(FIXTURE_SRC, fixture_dir)

    cfg = workdir / "weather.toml"
    cfg.write_text(
        template.format(
            db_path=str(workdir / "weather.db"),
            fixture_dir=str(fixture_dir),
            branding_path=str(BRANDING_EXAMPLE),
        )
    )
    monkeypatch.setenv("WEATHER_CONFIG", str(cfg))

    # Re-import to pick up the env var via lifespan.
    from weather_server.main import create_app

    return TestClient(create_app())


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Default station — history logging OFF (the shipping default)."""
    with _spin(tmp_path, monkeypatch, TOML_TEMPLATE) as tc:
        yield tc


@pytest.fixture
def logging_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Opt-in logging enabled — the logger prefills/records, so /history and
    /summary return data (today's behavior)."""
    with _spin(tmp_path, monkeypatch, LOGGING_TOML_TEMPLATE) as tc:
        yield tc
