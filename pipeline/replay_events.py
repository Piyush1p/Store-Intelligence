from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from pipeline.emit import read_jsonl


def post_batch(api: str, events: list[dict]) -> dict:
    payload = json.dumps({"events": events}).encode("utf-8")
    request = urllib.request.Request(
        f"{api.rstrip('/')}/events/ingest",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise RuntimeError(f"API returned {exc.code}: {body}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay JSONL events into the Store Intelligence API.")
    parser.add_argument("--events", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--interval-sec", type=float, default=0.5)
    args = parser.parse_args()

    events = read_jsonl(Path(args.events))
    sent = 0
    for start in range(0, len(events), args.batch_size):
        batch = events[start : start + args.batch_size]
        result = post_batch(args.api, batch)
        sent += len(batch)
        print({"sent": sent, **result})
        time.sleep(args.interval_sec)


if __name__ == "__main__":
    main()

