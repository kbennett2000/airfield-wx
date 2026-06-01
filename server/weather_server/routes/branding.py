"""GET /api/v1/branding.

The dashboard fetches this once on page load and uses it to fill every
[BRANDING] slot in the HTML. Schema is defined in branding.toml.example;
the route just passes the cached dict through verbatim.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/v1/branding")
async def get_branding(request: Request) -> dict[str, Any]:
    # app.state.branding is populated in lifespan startup from branding.py.
    # Starlette's State exposes attributes as Any, so we re-narrow here.
    branding: dict[str, Any] = request.app.state.branding
    return branding
