from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.analytics import compute_metrics


def detect_anomalies(
    current_events: list[dict[str, Any]],
    current_transactions: list[dict[str, Any]],
    history_events: list[dict[str, Any]],
    history_transactions: list[dict[str, Any]],
    known_zones: set[str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    current_metrics = compute_metrics(current_events, current_transactions)
    history_metrics = compute_metrics(history_events, history_transactions)
    anomalies: list[dict[str, Any]] = []

    depth = current_metrics["queue_depth"]
    if depth >= 8:
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL",
                "observed_value": depth,
                "suggested_action": "Open an additional billing counter immediately.",
            }
        )
    elif depth >= 5:
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "WARN",
                "observed_value": depth,
                "suggested_action": "Ask floor staff to monitor billing queue build-up.",
            }
        )

    baseline = history_metrics["conversion_rate"]
    current = current_metrics["conversion_rate"]
    if baseline > 0 and current < baseline * 0.65:
        severity = "CRITICAL" if current < baseline * 0.4 else "WARN"
        anomalies.append(
            {
                "type": "CONVERSION_DROP",
                "severity": severity,
                "observed_value": current,
                "baseline_value": baseline,
                "suggested_action": "Review staffing, queue wait time, and high-dwell zones with low purchase follow-through.",
            }
        )

    zones = set(known_zones or set())
    zones.update(event["zone_id"] for event in history_events + current_events if event.get("zone_id"))
    recent_cutoff = now - timedelta(minutes=30)
    recent_zones = {
        event["zone_id"]
        for event in current_events
        if event.get("zone_id") and event["timestamp"] >= recent_cutoff
    }
    for zone in sorted(zones - recent_zones):
        anomalies.append(
            {
                "type": "DEAD_ZONE",
                "severity": "INFO",
                "zone_id": zone,
                "suggested_action": "Check camera coverage and product-zone engagement for this zone.",
            }
        )

    return anomalies

