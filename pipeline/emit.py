from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_event(
    *,
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    zone_id: str | None = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.85,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": utc_iso(timestamp),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "metadata": metadata or {},
    }


def write_jsonl(events: Iterable[dict[str, Any]], output_path: str | Path) -> int:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
            count += 1
    return count


def read_jsonl(input_path: str | Path) -> list[dict[str, Any]]:
    path = Path(input_path)
    events = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events

