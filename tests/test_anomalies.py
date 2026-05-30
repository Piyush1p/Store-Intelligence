# PROMPT: Write anomaly detection tests for queue spikes, conversion drops against
# historical baseline, and dead zones with no visits in the last thirty minutes.
# CHANGES MADE: Added deterministic timestamps so tests do not depend on wall-clock time.

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.anomalies import detect_anomalies


NOW = datetime(2026, 4, 10, 18, 0, tzinfo=timezone.utc)


def ev(visitor, event_type, minutes_ago, zone=None, queue_depth=None):
    metadata = {}
    if queue_depth is not None:
        metadata["queue_depth"] = queue_depth
    return {
        "event_id": f"{visitor}-{event_type}-{minutes_ago}",
        "store_id": "ST1008",
        "camera_id": "CAM_1",
        "visitor_id": visitor,
        "event_type": event_type,
        "timestamp": NOW - timedelta(minutes=minutes_ago),
        "zone_id": zone,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": metadata,
    }


class AnomalyTests(unittest.TestCase):
    def test_queue_spike_and_dead_zone(self):
        current = [
            ev("VIS_A", "ENTRY", 5),
            ev("VIS_A", "BILLING_QUEUE_JOIN", 4, "CASH_COUNTER", queue_depth=8),
        ]
        anomalies = detect_anomalies(
            current,
            [],
            [],
            [],
            known_zones={"CASH_COUNTER", "SKINCARE"},
            now=NOW,
        )
        by_type = {item["type"]: item for item in anomalies}
        self.assertEqual(by_type["BILLING_QUEUE_SPIKE"]["severity"], "CRITICAL")
        self.assertIn("DEAD_ZONE", by_type)

    def test_conversion_drop_against_history(self):
        current = [ev("VIS_A", "ENTRY", 5)]
        history = [
            ev("VIS_OLD", "ENTRY", 60 * 24),
            ev("VIS_OLD", "BILLING_QUEUE_JOIN", 60 * 24 - 2, "CASH_COUNTER"),
        ]
        history_tx = [
            {
                "transaction_id": "TX_OLD",
                "store_id": "ST1008",
                "timestamp": history[1]["timestamp"] + timedelta(minutes=1),
                "basket_value_inr": 500.0,
                "metadata": {},
            }
        ]
        anomalies = detect_anomalies(current, [], history, history_tx, known_zones=set(), now=NOW)
        self.assertIn("CONVERSION_DROP", {item["type"] for item in anomalies})


if __name__ == "__main__":
    unittest.main()

