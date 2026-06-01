"""Tests for the branding loader + /api/v1/branding endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from weather_server.branding import _empty_branding, load_branding

# ── Loader: file precedence ───────────────────────────────────────────


def test_load_branding_reads_live_file(tmp_path: Path) -> None:
    live = tmp_path / "branding.toml"
    live.write_text(
        '[header]\ntagline = "live tagline"\n'
        '[taglines]\nrotating = ["a", "b"]\n'
    )
    result = load_branding(live)
    assert result["header"]["tagline"] == "live tagline"
    assert result["taglines"]["rotating"] == ["a", "b"]


def test_load_branding_falls_back_to_example_when_live_missing(tmp_path: Path) -> None:
    example = tmp_path / "branding.toml.example"
    example.write_text('[header]\ntagline = "from example"\n')
    # branding.toml itself does NOT exist.
    result = load_branding(tmp_path / "branding.toml")
    assert result["header"]["tagline"] == "from example"


def test_load_branding_returns_empty_schema_when_both_missing(tmp_path: Path) -> None:
    """No live file, no .example — dashboard must still get a valid
    shape so its JS can index without runtime errors."""
    result = load_branding(tmp_path / "nope.toml")
    assert result == _empty_branding()
    # The schema-shaped default has all the top-level keys the dashboard
    # might read.
    assert set(result.keys()) >= {
        "header", "footer", "browser_title", "states", "error", "taglines"
    }


def test_load_branding_normalizes_partial_file(tmp_path: Path) -> None:
    """A user who only edits [header] should still get the other
    sections populated with defaults — the API response shape is
    contractually stable."""
    live = tmp_path / "branding.toml"
    live.write_text('[header]\ntagline = "only header"\n')
    result = load_branding(live)
    assert result["header"]["tagline"] == "only header"
    # Missing sections get filled in from the empty schema.
    assert "footer" in result
    assert "states" in result
    assert "taglines" in result
    assert result["taglines"]["rotating"] == []


# ── Endpoint ──────────────────────────────────────────────────────────


def test_branding_endpoint_returns_loaded_blob(client: TestClient) -> None:
    """The route just passes app.state.branding through. The live file
    in this repo is the .example one (since branding.toml is gitignored
    and not present in tests)."""
    r = client.get("/api/v1/branding")
    assert r.status_code == 200
    body = r.json()
    # Schema shape: top-level sections always present.
    assert set(body.keys()) >= {
        "header", "footer", "browser_title", "states", "error", "taglines"
    }
    # The .example ships with placeholder strings in [header.tagline].
    assert isinstance(body["header"]["tagline"], str)
    # taglines.rotating is an array (may be empty).
    assert isinstance(body["taglines"]["rotating"], list)


def test_branding_endpoint_serves_same_content_across_calls(client: TestClient) -> None:
    """Cached in app.state; reload only happens on server restart. Two
    sequential requests should return byte-identical bodies."""
    r1 = client.get("/api/v1/branding")
    r2 = client.get("/api/v1/branding")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
