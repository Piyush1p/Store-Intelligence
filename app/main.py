from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from app.analytics import compute_funnel, compute_heatmap, compute_metrics, default_window
from app.anomalies import detect_anomalies
from app.layout import zones_for_store
from app.models import IngestError, IngestResponse, PosTransaction, StoreEvent
from app.storage import StoreRepository


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(message)s",
)
logger = logging.getLogger("store_intelligence")

app = FastAPI(title="Store Intelligence API", version="1.0.0")
repo = StoreRepository()


@app.middleware("http")
async def structured_logging(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        store_id = request.path_params.get("store_id") if hasattr(request, "path_params") else None
        event_count = getattr(request.state, "event_count", None)
        logger.info(
            {
                "trace_id": trace_id,
                "endpoint": request.url.path,
                "store_id": store_id,
                "latency_ms": latency_ms,
                "event_count": event_count,
                "status_code": status_code,
            }
        )


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def request_window(start: str | None, end: str | None) -> tuple[datetime, datetime]:
    parsed_start = parse_dt(start)
    parsed_end = parse_dt(end)
    if parsed_start and parsed_end:
        return parsed_start, parsed_end
    default_start, default_end = default_window()
    return parsed_start or default_start, parsed_end or default_end


def events_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        return payload["events"]
    raise ValueError("payload must be a list of events or an object with an events list")


@app.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        raw_events = events_payload(payload)
        request.state.event_count = len(raw_events)
        if len(raw_events) > 500:
            return JSONResponse(
                status_code=413,
                content={
                    "accepted": 0,
                    "duplicates": 0,
                    "rejected": len(raw_events),
                    "errors": [{"index": -1, "event_id": None, "error": "batch limit is 500 events"}],
                },
            )
        valid: list[StoreEvent] = []
        errors: list[IngestError] = []
        for index, raw in enumerate(raw_events):
            event_id = raw.get("event_id") if isinstance(raw, dict) else None
            try:
                valid.append(StoreEvent.model_validate(raw))
            except ValidationError as exc:
                errors.append(IngestError(index=index, event_id=event_id, error=str(exc.errors()[0])))
        accepted, duplicates = repo.insert_events(valid)
    except (ValueError, TypeError) as exc:
        return JSONResponse(
            status_code=400,
            content={
                "accepted": 0,
                "duplicates": 0,
                "rejected": 1,
                "errors": [{"index": -1, "event_id": None, "error": str(exc)}],
            },
        )
    except sqlite3.Error:
        return JSONResponse(
            status_code=503,
            content={
                "accepted": 0,
                "duplicates": 0,
                "rejected": len(raw_events) if "raw_events" in locals() else 0,
                "errors": [{"index": -1, "event_id": None, "error": "database unavailable"}],
            },
        )

    response = IngestResponse(
        accepted=accepted,
        duplicates=duplicates,
        rejected=len(errors),
        errors=errors,
    )
    status = 207 if errors and accepted else 400 if errors and not accepted else 200
    return JSONResponse(status_code=status, content=response.model_dump())


@app.post("/pos/ingest")
async def ingest_pos(payload: dict[str, list[dict[str, Any]]]) -> JSONResponse:
    raw_transactions = payload.get("transactions", [])
    valid: list[PosTransaction] = []
    errors = []
    for index, raw in enumerate(raw_transactions):
        try:
            valid.append(PosTransaction.model_validate(raw))
        except ValidationError as exc:
            errors.append({"index": index, "error": str(exc.errors()[0])})
    try:
        accepted, duplicates = repo.insert_pos_transactions(valid)
    except sqlite3.Error:
        return JSONResponse(status_code=503, content={"error": "database unavailable"})
    return JSONResponse(
        status_code=207 if errors and accepted else 400 if errors and not accepted else 200,
        content={"accepted": accepted, "duplicates": duplicates, "rejected": len(errors), "errors": errors},
    )


@app.get("/stores/{store_id}/metrics")
def metrics(
    store_id: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    window_start, window_end = request_window(start, end)
    events = repo.list_events(store_id, window_start, window_end)
    transactions = repo.list_pos_transactions(store_id, window_start, window_end)
    return {
        "store_id": store_id,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        **compute_metrics(events, transactions),
    }


@app.get("/stores/{store_id}/funnel")
def funnel(
    store_id: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    window_start, window_end = request_window(start, end)
    events = repo.list_events(store_id, window_start, window_end)
    transactions = repo.list_pos_transactions(store_id, window_start, window_end)
    return {
        "store_id": store_id,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        **compute_funnel(events, transactions),
    }


@app.get("/stores/{store_id}/heatmap")
def heatmap(
    store_id: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    window_start, window_end = request_window(start, end)
    events = repo.list_events(store_id, window_start, window_end)
    return {
        "store_id": store_id,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        **compute_heatmap(events),
    }


@app.get("/stores/{store_id}/anomalies")
def anomalies(
    store_id: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    window_start, window_end = request_window(start, end)
    history_start = window_start - timedelta(days=7)
    events = repo.list_events(store_id, window_start, window_end)
    transactions = repo.list_pos_transactions(store_id, window_start, window_end)
    history_events = repo.list_events(store_id, history_start, window_start)
    history_transactions = repo.list_pos_transactions(store_id, history_start, window_start)
    known = zones_for_store(store_id) | repo.known_zones(store_id)
    return {
        "store_id": store_id,
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "anomalies": detect_anomalies(
            events,
            transactions,
            history_events,
            history_transactions,
            known_zones=known,
            now=window_end,
        ),
    }


@app.get("/health")
def health() -> JSONResponse:
    try:
        repo.healthcheck()
        last = repo.last_event_by_store()
    except sqlite3.Error:
        return JSONResponse(status_code=503, content={"status": "DEGRADED", "error": "database unavailable"})
    now = datetime.now(timezone.utc)
    stores = {}
    for store_id, last_timestamp in last.items():
        parsed = parse_dt(last_timestamp)
        lag_seconds = (now - parsed).total_seconds() if parsed else None
        stores[store_id] = {
            "last_event_timestamp": last_timestamp,
            "lag_seconds": lag_seconds,
            "warning": "STALE_FEED" if lag_seconds is not None and lag_seconds > 600 else None,
        }
    return JSONResponse(content={"status": "OK", "stores": stores})


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root() -> str:
    return dashboard()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return """
<!doctype html>
<html>
<head>
  <title>Store Intelligence Dashboard</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 32px; color: #17202a; }
    label, input, button { font-size: 14px; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 12px; margin-top: 20px; }
    .tile { border: 1px solid #d8dee6; border-radius: 8px; padding: 14px; }
    .value { font-size: 28px; font-weight: 700; margin-top: 8px; }
    pre { background: #f7f8fa; padding: 12px; overflow: auto; }
  </style>
</head>
<body>
  <h1>Store Intelligence</h1>
  <label>Store ID <input id="store" value="ST1008"></label>
  <button onclick="refresh()">Refresh</button>
  <div class="grid">
    <div class="tile">Visitors<div class="value" id="visitors">-</div></div>
    <div class="tile">Conversion<div class="value" id="conversion">-</div></div>
    <div class="tile">Queue<div class="value" id="queue">-</div></div>
    <div class="tile">Abandonment<div class="value" id="abandonment">-</div></div>
  </div>
  <h2>Raw Metrics</h2>
  <pre id="raw"></pre>
  <script>
    async function refresh() {
      const store = document.getElementById('store').value;
      const res = await fetch(`/stores/${store}/metrics`);
      const data = await res.json();
      document.getElementById('visitors').textContent = data.unique_visitors;
      document.getElementById('conversion').textContent = `${Math.round(data.conversion_rate * 100)}%`;
      document.getElementById('queue').textContent = data.queue_depth;
      document.getElementById('abandonment').textContent = `${Math.round(data.abandonment_rate * 100)}%`;
      document.getElementById('raw').textContent = JSON.stringify(data, null, 2);
    }
    setInterval(refresh, 2000);
    refresh();
  </script>
</body>
</html>
"""
