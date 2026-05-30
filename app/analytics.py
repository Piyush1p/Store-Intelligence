from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


BILLING_ZONE_HINTS = ("BILL", "CASH", "COUNTER", "CHECKOUT", "POS")


def default_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def is_customer_event(event: dict[str, Any]) -> bool:
    return not event.get("is_staff", False)


def is_billing_zone(zone_id: str | None) -> bool:
    if not zone_id:
        return False
    upper = zone_id.upper()
    return any(hint in upper for hint in BILLING_ZONE_HINTS)


def visitor_sets(events: list[dict[str, Any]]) -> dict[str, set[str]]:
    customer_events = [event for event in events if is_customer_event(event)]
    entry = {
        event["visitor_id"]
        for event in customer_events
        if event["event_type"] in {"ENTRY", "REENTRY"}
    }
    any_seen = {event["visitor_id"] for event in customer_events}
    zone_visit = {
        event["visitor_id"]
        for event in customer_events
        if event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL"} and event.get("zone_id")
    }
    billing = {
        event["visitor_id"]
        for event in customer_events
        if event["event_type"] == "BILLING_QUEUE_JOIN" or is_billing_zone(event.get("zone_id"))
    }
    return {
        "entry": entry or any_seen,
        "any_seen": any_seen,
        "zone_visit": zone_visit,
        "billing": billing,
    }


def correlate_purchases(
    events: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    lookback: timedelta = timedelta(minutes=5),
) -> set[str]:
    billing_events = [
        event
        for event in events
        if is_customer_event(event)
        and (event["event_type"] == "BILLING_QUEUE_JOIN" or is_billing_zone(event.get("zone_id")))
    ]
    converted: set[str] = set()
    for tx in transactions:
        candidates = [
            event
            for event in billing_events
            if tx["timestamp"] - lookback <= event["timestamp"] <= tx["timestamp"]
        ]
        if not candidates:
            continue
        latest = max(candidates, key=lambda event: event["timestamp"])
        converted.add(latest["visitor_id"])
    return converted


def compute_metrics(
    events: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    sets = visitor_sets(events)
    visitors = sets["entry"]
    converted = correlate_purchases(events, transactions)

    dwell_by_zone: dict[str, list[int]] = defaultdict(list)
    for event in events:
        if (
            is_customer_event(event)
            and event["event_type"] == "ZONE_DWELL"
            and event.get("zone_id")
        ):
            dwell_by_zone[event["zone_id"]].append(event["dwell_ms"])

    queue_depth = 0
    for event in events:
        depth = event.get("metadata", {}).get("queue_depth")
        if isinstance(depth, int):
            queue_depth = depth

    join_visitors = {
        event["visitor_id"]
        for event in events
        if is_customer_event(event) and event["event_type"] == "BILLING_QUEUE_JOIN"
    }
    abandon_visitors = {
        event["visitor_id"]
        for event in events
        if is_customer_event(event) and event["event_type"] == "BILLING_QUEUE_ABANDON"
    }

    visitor_count = len(visitors)
    return {
        "unique_visitors": visitor_count,
        "conversion_rate": round(len(converted) / visitor_count, 4) if visitor_count else 0.0,
        "converted_visitors": len(converted),
        "transaction_count": len(transactions),
        "avg_dwell_per_zone_ms": {
            zone: round(sum(values) / len(values), 2) for zone, values in sorted(dwell_by_zone.items())
        },
        "queue_depth": queue_depth,
        "abandonment_rate": round(len(abandon_visitors) / len(join_visitors), 4)
        if join_visitors
        else 0.0,
    }


def compute_funnel(events: list[dict[str, Any]], transactions: list[dict[str, Any]]) -> dict[str, Any]:
    sets = visitor_sets(events)
    purchase = correlate_purchases(events, transactions)
    stages = [
        ("entry", sets["entry"]),
        ("zone_visit", sets["zone_visit"]),
        ("billing_queue", sets["billing"]),
        ("purchase", purchase),
    ]
    output = []
    previous_count: int | None = None
    for name, visitors in stages:
        count = len(visitors)
        if previous_count is None:
            dropoff_pct = 0.0
        elif previous_count == 0:
            dropoff_pct = 0.0
        else:
            dropoff_pct = round((previous_count - count) / previous_count, 4)
        output.append({"stage": name, "count": count, "dropoff_pct": dropoff_pct})
        previous_count = count
    return {"unit": "visitor_session", "stages": output}


def compute_heatmap(events: list[dict[str, Any]]) -> dict[str, Any]:
    sets = visitor_sets(events)
    visits: Counter[str] = Counter()
    dwell: dict[str, list[int]] = defaultdict(list)
    for event in events:
        if not is_customer_event(event) or not event.get("zone_id"):
            continue
        if event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL"}:
            visits[event["zone_id"]] += 1
        if event["event_type"] == "ZONE_DWELL":
            dwell[event["zone_id"]].append(event["dwell_ms"])

    max_visits = max(visits.values(), default=0)
    zones = []
    for zone in sorted(visits):
        normalized = round((visits[zone] / max_visits) * 100, 2) if max_visits else 0.0
        values = dwell.get(zone, [])
        zones.append(
            {
                "zone_id": zone,
                "visits": visits[zone],
                "avg_dwell_ms": round(sum(values) / len(values), 2) if values else 0.0,
                "heat_score": normalized,
            }
        )
    return {
        "data_confidence": "LOW" if len(sets["entry"]) < 20 else "OK",
        "zones": zones,
    }

