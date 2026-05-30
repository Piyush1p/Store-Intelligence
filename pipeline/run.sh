#!/usr/bin/env bash
set -euo pipefail

API="${API:-http://127.0.0.1:8000}"
POS_CSV="${POS_CSV:-}"
VIDEO="${VIDEO:-}"
CAMERA_MANIFEST="${CAMERA_MANIFEST:-}"
STORE_ID="${STORE_ID:-ST1008}"

mkdir -p data

if [[ -n "$CAMERA_MANIFEST" ]]; then
  python -m pipeline.process_cameras --manifest "$CAMERA_MANIFEST" --output data/detected_events.jsonl
  python -m pipeline.replay_events --events data/detected_events.jsonl --api "$API" --batch-size 50 --interval-sec 0.25
fi

if [[ -n "$POS_CSV" ]]; then
  python -m pipeline.load_pos --pos-csv "$POS_CSV" --api "$API" --store-id "$STORE_ID"
  python -m pipeline.simulate_events --pos-csv "$POS_CSV" --store-id "$STORE_ID" --output data/simulated_events.jsonl
  python -m pipeline.replay_events --events data/simulated_events.jsonl --api "$API" --batch-size 50 --interval-sec 0.25
fi

if [[ -n "$VIDEO" ]]; then
  python -m pipeline.detect --video "$VIDEO" --store-id "$STORE_ID" --output data/detected_events.jsonl
  python -m pipeline.replay_events --events data/detected_events.jsonl --api "$API" --batch-size 50 --interval-sec 0.25
fi

if [[ -z "$POS_CSV" && -z "$VIDEO" && -z "$CAMERA_MANIFEST" ]]; then
  echo "Set CAMERA_MANIFEST, POS_CSV, or VIDEO to produce events."
  exit 2
fi
