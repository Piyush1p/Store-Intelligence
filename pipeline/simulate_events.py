from __future__ import annotations

import argparse
import csv
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.emit import make_event, write_jsonl


ZONE_BY_DEPARTMENT = {
    "skin": "SKINCARE",
    "makeup": "MAKEUP",
    "bath-and-body": "BATH_AND_BODY",
    "personal-care": "PERSONAL_CARE",
    "hair-care": "HAIR_CARE",
}


def stable_visitor_id(key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"VIS_{digest}"


def parse_timestamp(row: dict[str, str]) -> datetime:
    if row.get("timestamp"):
        return datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).astimezone(timezone.utc)
    order_date = row.get("order_date") or row.get("date")
    order_time = row.get("order_time") or row.get("time") or "00:00:00"
    if not order_date:
        raise ValueError("POS row does not contain timestamp or order_date")
    for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{order_date} {order_time}", fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"unsupported POS timestamp: {order_date} {order_time}")


def load_pos_groups(path: Path, store_id_override: str | None = None) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    departments: dict[str, list[str]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tx_id = (
                row.get("transaction_id")
                or row.get("invoice_number")
                or row.get("order_id")
                or f"ROW_{reader.line_num}"
            )
            store_id = store_id_override or row.get("store_id") or "STORE_UNKNOWN"
            amount = float(row.get("basket_value_inr") or row.get("total_amount") or row.get("NMV") or 0)
            timestamp = parse_timestamp(row)
            if tx_id not in groups:
                groups[tx_id] = {
                    "transaction_id": tx_id,
                    "store_id": store_id,
                    "timestamp": timestamp,
                    "basket_value_inr": 0.0,
                }
            groups[tx_id]["basket_value_inr"] += amount
            if row.get("dep_name"):
                departments[tx_id].append(row["dep_name"])

    output = []
    for tx_id, tx in sorted(groups.items(), key=lambda item: item[1]["timestamp"]):
        deps = departments.get(tx_id) or ["skin"]
        preferred = max(set(deps), key=deps.count)
        tx["zone_id"] = ZONE_BY_DEPARTMENT.get(preferred, preferred.upper().replace("-", "_"))
        output.append(tx)
    return output


def simulate_events(transactions: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, tx in enumerate(transactions[:limit]):
        visitor_id = stable_visitor_id(tx["transaction_id"])
        entry_at = tx["timestamp"] - timedelta(minutes=12 + index % 5)
        zone_at = tx["timestamp"] - timedelta(minutes=9 + index % 3)
        billing_at = tx["timestamp"] - timedelta(minutes=3)
        exit_at = tx["timestamp"] + timedelta(minutes=2)
        zone_id = tx["zone_id"]
        queue_depth = 1 + (index % 7)

        events.extend(
            [
                make_event(
                    store_id=tx["store_id"],
                    camera_id="CAM_ENTRY_01",
                    visitor_id=visitor_id,
                    event_type="ENTRY",
                    timestamp=entry_at,
                    confidence=0.92,
                    metadata={"source": "pos_simulator", "session_seq": 1},
                ),
                make_event(
                    store_id=tx["store_id"],
                    camera_id="CAM_FLOOR_01",
                    visitor_id=visitor_id,
                    event_type="ZONE_ENTER",
                    timestamp=zone_at,
                    zone_id=zone_id,
                    confidence=0.83,
                    metadata={"source": "pos_simulator", "session_seq": 2},
                ),
                make_event(
                    store_id=tx["store_id"],
                    camera_id="CAM_FLOOR_01",
                    visitor_id=visitor_id,
                    event_type="ZONE_DWELL",
                    timestamp=zone_at + timedelta(seconds=35),
                    zone_id=zone_id,
                    dwell_ms=35000 + (index % 4) * 10000,
                    confidence=0.79,
                    metadata={"source": "pos_simulator", "session_seq": 3},
                ),
                make_event(
                    store_id=tx["store_id"],
                    camera_id="CAM_BILLING_01",
                    visitor_id=visitor_id,
                    event_type="BILLING_QUEUE_JOIN",
                    timestamp=billing_at,
                    zone_id="CASH_COUNTER",
                    confidence=0.88,
                    metadata={"source": "pos_simulator", "queue_depth": queue_depth, "session_seq": 4},
                ),
                make_event(
                    store_id=tx["store_id"],
                    camera_id="CAM_ENTRY_01",
                    visitor_id=visitor_id,
                    event_type="EXIT",
                    timestamp=exit_at,
                    confidence=0.9,
                    metadata={"source": "pos_simulator", "session_seq": 5},
                ),
            ]
        )

        if index % 11 == 0:
            staff_id = f"STAFF_{index:03d}"
            events.append(
                make_event(
                    store_id=tx["store_id"],
                    camera_id="CAM_FLOOR_01",
                    visitor_id=staff_id,
                    event_type="ZONE_DWELL",
                    timestamp=zone_at,
                    zone_id=zone_id,
                    dwell_ms=120000,
                    is_staff=True,
                    confidence=0.86,
                    metadata={"source": "pos_simulator", "uniform_signal": "apron_color"},
                )
            )
    return sorted(events, key=lambda event: event["timestamp"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate validation events from an external POS CSV.")
    parser.add_argument("--pos-csv", required=True, help="Path to POS CSV. Do not commit this file.")
    parser.add_argument("--output", default="data/simulated_events.jsonl")
    parser.add_argument("--store-id", default=None, help="Override store_id when normalising vendor POS exports.")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    transactions = load_pos_groups(Path(args.pos_csv), args.store_id)
    events = simulate_events(transactions, args.limit)
    count = write_jsonl(events, args.output)
    print(f"wrote {count} events to {args.output}")


if __name__ == "__main__":
    main()

