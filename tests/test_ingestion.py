# PROMPT: Generate tests for an event ingestion layer that validates schema compliance,
# deduplicates by event_id, and handles malformed records without crashing.
# CHANGES MADE: Replaced generated HTTP-client tests with repository-level tests so they
# run without requiring FastAPI or network-installed dependencies in the local workspace.

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from pydantic import ValidationError

from app.models import StoreEvent
from app.storage import StoreRepository


def event(**overrides):
    data = {
        "event_id": str(uuid4()),
        "store_id": "ST1008",
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_001",
        "event_type": "ENTRY",
        "timestamp": datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc),
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.91,
        "metadata": {"session_seq": 1},
    }
    data.update(overrides)
    return StoreEvent.model_validate(data)


class IngestionTests(unittest.TestCase):
    def test_idempotent_insert_counts_duplicate(self):
        with TemporaryDirectory() as tmp:
            repo = StoreRepository(str(Path(tmp) / "events.db"))
            first = event()
            accepted, duplicates = repo.insert_events([first, first])
            self.assertEqual(accepted, 1)
            self.assertEqual(duplicates, 1)
            self.assertEqual(len(repo.list_events("ST1008")), 1)
            repo.close()

    def test_schema_rejects_bad_confidence(self):
        with self.assertRaises(ValidationError):
            event(confidence=1.5)

    def test_naive_timestamp_is_normalized_to_utc(self):
        parsed = event(timestamp=datetime(2026, 4, 10, 10, 0))
        self.assertIsNotNone(parsed.timestamp.tzinfo)
        self.assertEqual(parsed.timestamp.utcoffset().total_seconds(), 0)


if __name__ == "__main__":
    unittest.main()
