# PROMPT: Create tests for store metrics that cover staff exclusion, re-entry,
# session-level funnel counting, POS conversion matching, and zero-purchase behavior.
# CHANGES MADE: Tightened expected values to the exact challenge semantics and added a
# zero-purchase assertion because stores with traffic but no transactions must not crash.

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.analytics import compute_funnel, compute_heatmap, compute_metrics


BASE = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)


def ev(visitor, event_type, minutes, zone=None, staff=False, dwell=0, queue_depth=None):
    metadata = {}
    if queue_depth is not None:
        metadata["queue_depth"] = queue_depth
    return {
        "event_id": f"{visitor}-{event_type}-{minutes}",
        "store_id": "ST1008",
        "camera_id": "CAM_1",
        "visitor_id": visitor,
        "event_type": event_type,
        "timestamp": BASE + timedelta(minutes=minutes),
        "zone_id": zone,
        "dwell_ms": dwell,
        "is_staff": staff,
        "confidence": 0.9,
        "metadata": metadata,
    }


class MetricsTests(unittest.TestCase):
    def test_staff_excluded_and_purchase_correlated(self):
        events = [
            ev("VIS_A", "ENTRY", 0),
            ev("VIS_A", "ZONE_ENTER", 2, "SKINCARE"),
            ev("VIS_A", "ZONE_DWELL", 3, "SKINCARE", dwell=40000),
            ev("VIS_A", "BILLING_QUEUE_JOIN", 8, "CASH_COUNTER", queue_depth=3),
            ev("VIS_B", "ENTRY", 1),
            ev("VIS_B", "ZONE_ENTER", 4, "MAKEUP"),
            ev("VIS_B", "REENTRY", 6),
            ev("STAFF_1", "ENTRY", 1, staff=True),
            ev("STAFF_1", "ZONE_DWELL", 5, "SKINCARE", staff=True, dwell=120000),
        ]
        transactions = [
            {
                "transaction_id": "TX_1",
                "store_id": "ST1008",
                "timestamp": BASE + timedelta(minutes=10),
                "basket_value_inr": 500.0,
                "metadata": {},
            }
        ]
        metrics = compute_metrics(events, transactions)
        self.assertEqual(metrics["unique_visitors"], 2)
        self.assertEqual(metrics["converted_visitors"], 1)
        self.assertEqual(metrics["conversion_rate"], 0.5)
        self.assertEqual(metrics["avg_dwell_per_zone_ms"], {"SKINCARE": 40000.0})
        self.assertEqual(metrics["queue_depth"], 3)

        funnel = compute_funnel(events, transactions)
        counts = {stage["stage"]: stage["count"] for stage in funnel["stages"]}
        self.assertEqual(counts, {"entry": 2, "zone_visit": 2, "billing_queue": 1, "purchase": 1})

    def test_zero_purchase_store_returns_zero_conversion(self):
        events = [ev("VIS_A", "ENTRY", 0), ev("VIS_A", "ZONE_ENTER", 1, "MAKEUP")]
        metrics = compute_metrics(events, [])
        self.assertEqual(metrics["unique_visitors"], 1)
        self.assertEqual(metrics["conversion_rate"], 0.0)
        self.assertEqual(metrics["abandonment_rate"], 0.0)

    def test_heatmap_low_confidence_until_twenty_sessions(self):
        events = [ev("VIS_A", "ENTRY", 0), ev("VIS_A", "ZONE_ENTER", 1, "MAKEUP")]
        heatmap = compute_heatmap(events)
        self.assertEqual(heatmap["data_confidence"], "LOW")
        self.assertEqual(heatmap["zones"][0]["heat_score"], 100.0)


if __name__ == "__main__":
    unittest.main()

