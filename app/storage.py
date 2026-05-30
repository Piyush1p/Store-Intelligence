from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.models import PosTransaction, StoreEvent


def default_db_path() -> str:
    return os.getenv("STORE_INTEL_DB", "data/store_intelligence.db")


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class StoreRepository:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or default_db_path()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    visitor_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    zone_id TEXT,
                    dwell_ms INTEGER NOT NULL,
                    is_staff INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_store_time ON events(store_id, timestamp)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_visitor ON events(store_id, visitor_id)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pos_transactions (
                    transaction_id TEXT NOT NULL,
                    store_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    basket_value_inr REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (store_id, transaction_id)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pos_store_time ON pos_transactions(store_id, timestamp)"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def healthcheck(self) -> bool:
        with self._lock:
            self._conn.execute("SELECT 1").fetchone()
        return True

    def insert_events(self, events: Iterable[StoreEvent]) -> tuple[int, int]:
        accepted = 0
        duplicates = 0
        now = iso(datetime.now(timezone.utc))
        with self._lock, self._conn:
            for event in events:
                cursor = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_id, store_id, camera_id, visitor_id, event_type,
                        timestamp, zone_id, dwell_ms, is_staff, confidence,
                        metadata_json, ingested_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event.event_id),
                        event.store_id,
                        event.camera_id,
                        event.visitor_id,
                        event.event_type.value,
                        iso(event.timestamp),
                        event.zone_id,
                        event.dwell_ms,
                        int(event.is_staff),
                        event.confidence,
                        json.dumps(event.metadata, sort_keys=True),
                        now,
                    ),
                )
                if cursor.rowcount == 1:
                    accepted += 1
                else:
                    duplicates += 1
        return accepted, duplicates

    def insert_pos_transactions(self, transactions: Iterable[PosTransaction]) -> tuple[int, int]:
        accepted = 0
        duplicates = 0
        with self._lock, self._conn:
            for tx in transactions:
                cursor = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO pos_transactions (
                        transaction_id, store_id, timestamp, basket_value_inr, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        tx.transaction_id,
                        tx.store_id,
                        iso(tx.timestamp),
                        tx.basket_value_inr,
                        json.dumps(tx.metadata, sort_keys=True),
                    ),
                )
                if cursor.rowcount == 1:
                    accepted += 1
                else:
                    duplicates += 1
        return accepted, duplicates

    def list_events(
        self,
        store_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM events WHERE store_id = ?"
        params: list[Any] = [store_id]
        if start is not None:
            query += " AND timestamp >= ?"
            params.append(iso(start))
        if end is not None:
            query += " AND timestamp <= ?"
            params.append(iso(end))
        query += " ORDER BY timestamp ASC, event_id ASC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._event_row(row) for row in rows]

    def list_pos_transactions(
        self,
        store_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM pos_transactions WHERE store_id = ?"
        params: list[Any] = [store_id]
        if start is not None:
            query += " AND timestamp >= ?"
            params.append(iso(start))
        if end is not None:
            query += " AND timestamp <= ?"
            params.append(iso(end))
        query += " ORDER BY timestamp ASC, transaction_id ASC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._pos_row(row) for row in rows]

    def last_event_by_store(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT store_id, MAX(timestamp) AS last_timestamp FROM events GROUP BY store_id"
            ).fetchall()
        return {row["store_id"]: row["last_timestamp"] for row in rows}

    def known_zones(self, store_id: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT zone_id FROM events WHERE store_id = ? AND zone_id IS NOT NULL",
                (store_id,),
            ).fetchall()
        return {row["zone_id"] for row in rows if row["zone_id"]}

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "store_id": row["store_id"],
            "camera_id": row["camera_id"],
            "visitor_id": row["visitor_id"],
            "event_type": row["event_type"],
            "timestamp": parse_iso(row["timestamp"]),
            "zone_id": row["zone_id"],
            "dwell_ms": row["dwell_ms"],
            "is_staff": bool(row["is_staff"]),
            "confidence": row["confidence"],
            "metadata": json.loads(row["metadata_json"]),
        }

    @staticmethod
    def _pos_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "transaction_id": row["transaction_id"],
            "store_id": row["store_id"],
            "timestamp": parse_iso(row["timestamp"]),
            "basket_value_inr": row["basket_value_inr"],
            "metadata": json.loads(row["metadata_json"]),
        }

