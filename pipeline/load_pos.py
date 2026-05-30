from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from pipeline.simulate_events import load_pos_groups


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Load external POS CSV into the API.")
    parser.add_argument("--pos-csv", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--store-id", default=None)
    args = parser.parse_args()

    transactions = load_pos_groups(Path(args.pos_csv), args.store_id)
    payload = {
        "transactions": [
            {
                "transaction_id": tx["transaction_id"],
                "store_id": tx["store_id"],
                "timestamp": tx["timestamp"].isoformat().replace("+00:00", "Z"),
                "basket_value_inr": round(tx["basket_value_inr"], 2),
                "metadata": {"source": "pos_csv_loader", "zone_hint": tx.get("zone_id")},
            }
            for tx in transactions
        ]
    }
    print(post_json(f"{args.api.rstrip('/')}/pos/ingest", payload))


if __name__ == "__main__":
    main()

