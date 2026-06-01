"""GET /api/v1/history/{sensor_id}.

Only `outdoor` is logged; any other sensor_id returns 404
`history_not_available` per 02-api-design.md.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from .. import db as db_module
from ..responses import HISTORY_GROUPS, build_history_row
from ..schemas import HistoryResponse, HistoryRow

router = APIRouter()


BucketLiteral = Literal["raw", "60", "300", "900", "3600", "auto"]


@router.get(
    "/api/v1/history/{sensor_id}",
    response_model=HistoryResponse,
    response_model_exclude_none=False,
)
async def get_history(
    sensor_id: str,
    request: Request,
    hours: int = Query(24, ge=1, le=24 * 365),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
    bucket: BucketLiteral = "auto",
    include: str = "weather",
) -> HistoryResponse:
    config = request.app.state.config
    db = request.app.state.db

    sensor_cfg = config.sensor_by_id(sensor_id)
    if sensor_cfg is None:
        raise HTTPException(status_code=404, detail=("sensor_not_found", sensor_id))
    if sensor_cfg.role != "outdoor":
        raise HTTPException(status_code=404, detail=("history_not_available", sensor_id))

    include_groups = _parse_include(include)
    from_ts, to_ts, span_hours = _resolve_window(from_, to, hours)
    from_dt = datetime.fromtimestamp(from_ts, tz=UTC)
    to_dt = datetime.fromtimestamp(to_ts, tz=UTC)

    bucket_seconds = _resolve_bucket(bucket, span_hours)

    raw_rows = db_module.outdoor_readings_in_range(db, from_ts, to_ts)
    bucketed = _bucket_rows(raw_rows, bucket_seconds) if bucket_seconds > 0 else raw_rows

    rows: list[HistoryRow] = [
        HistoryRow.model_validate(build_history_row(row, sensor_cfg, include_groups))
        for row in bucketed
    ]

    return HistoryResponse(
        sensor_id=sensor_id,
        from_=from_dt,
        to=to_dt,
        bucket_seconds=bucket_seconds,
        row_count=len(rows),
        rows=rows,
    )


def _parse_iso_ts(value: str, field: str) -> int:
    """Parse an ISO 8601 datetime to a UTC epoch. 400 on malformed input."""
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=("bad_request", f"invalid ISO 8601 datetime for {field!r}: {value!r}"),
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _resolve_window(from_: str | None, to: str | None, hours: int) -> tuple[int, int, int]:
    """Resolve the query window to (from_ts, to_ts, span_hours).

    `from`/`to` (ISO 8601) take precedence over `hours` when supplied; `to`
    defaults to now. Returns the span in whole hours so `bucket=auto` still
    works. Raises 400 on malformed timestamps or a non-positive window.
    """
    now_ts = int(datetime.now(UTC).timestamp())
    if from_ is None and to is None:
        return now_ts - hours * 3600, now_ts, hours

    to_ts = _parse_iso_ts(to, "to") if to is not None else now_ts
    from_ts = _parse_iso_ts(from_, "from") if from_ is not None else to_ts - hours * 3600
    if from_ts >= to_ts:
        raise HTTPException(
            status_code=400,
            detail=("bad_request", "'from' must be earlier than 'to'"),
        )
    return from_ts, to_ts, max(1, (to_ts - from_ts) // 3600)


def _parse_include(value: str) -> set[str]:
    groups = {g.strip() for g in value.split(",") if g.strip()}
    unknown = groups - set(HISTORY_GROUPS)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=("bad_request", f"unknown include group(s): {sorted(unknown)}"),
        )
    return groups


def _resolve_bucket(bucket: str, hours: int) -> int:
    if bucket == "raw":
        return 0
    if bucket == "auto":
        if hours <= 1:
            return 0
        if hours <= 6:
            return 60
        if hours <= 24:
            return 300
        if hours <= 24 * 7:
            return 1800
        return 3600
    return int(bucket)


def _bucket_rows(
    rows: list[sqlite3.Row], bucket_seconds: int
) -> list[sqlite3.Row] | list[dict[str, Any]]:
    """Group rows into buckets of `bucket_seconds` and average continuous
    quantities. Returns one synthetic row per bucket carrying the mean
    timestamp and means for the numeric columns. When no bucketing is
    requested (bucket_seconds <= 0), the raw rows pass through unchanged.
    """
    if not rows or bucket_seconds <= 0:
        return rows
    out: list[dict[str, Any]] = []
    current_bucket: list[sqlite3.Row] = []
    current_start: int | None = None
    for row in rows:
        ts = int(row["timestamp"])
        b_start = (ts // bucket_seconds) * bucket_seconds
        if current_start is None:
            current_start = b_start
        if b_start != current_start:
            out.append(_aggregate_bucket(current_bucket, current_start))
            current_bucket = []
            current_start = b_start
        current_bucket.append(row)
    if current_bucket and current_start is not None:
        out.append(_aggregate_bucket(current_bucket, current_start))
    return out


def _aggregate_bucket(rows: list[sqlite3.Row], bucket_start: int) -> dict[str, Any]:
    """Mean for continuous cols, most-recent for discrete cols."""
    continuous = (
        "temperature_c",
        "humidity_pct",
        "pressure_pa",
        "lux",
        "ir",
        "visible",
        "full_spectrum",
        "altitude_m",
        "speed_kmh",
        "course_deg",
    )
    last_row = rows[-1]
    keys = last_row.keys()
    agg: dict[str, Any] = {"timestamp": bucket_start}
    for k in keys:
        if k in {"id", "timestamp"}:
            continue
        if k in continuous:
            vals = [r[k] for r in rows if r[k] is not None]
            agg[k] = sum(vals) / len(vals) if vals else None
        else:
            agg[k] = last_row[k]
    return agg
