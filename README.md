# Store Intelligence System

AI-powered store analytics built on raw CCTV-derived events. This covers the full required contract: event ingestion, idempotency, real-time metrics, funnel logic, heatmap payloads, anomaly detection, health checks, and a live dashboard.

The challenge files didn't include raw CCTV clips, so I've kept datasets and video files out of source control entirely. The pipeline takes external clip paths for detection, and there's a POS-driven event simulator so the full API flow can be exercised end-to-end using local files that stay outside the repo.

## Setup (5 commands)

```bash
git clone <your-repo-url>
cd store-intelligence
docker compose up --build
python -m pipeline.load_pos --pos-csv "/path/to/pos_transactions.csv" --api http://127.0.0.1:8000 --store-id ST1008
python -m pipeline.simulate_events --pos-csv "/path/to/pos_transactions.csv" --store-id ST1008 --output data/simulated_events.jsonl && python -m pipeline.replay_events --events data/simulated_events.jsonl --api http://127.0.0.1:8000
```

Dashboard is live at `http://127.0.0.1:8000/dashboard`.

## Running Against CCTV Clips

Pass an external video path — don't commit videos or generated JSONL files.

```bash
python -m pipeline.detect \
  --video "/path/to/entry_camera_clip.mp4" \
  --store-id ST1008 \
  --camera-id CAM_ENTRY_01 \
  --started-at 2026-04-10T12:00:00Z \
  --output data/detected_events.jsonl

python -m pipeline.replay_events --events data/detected_events.jsonl --api http://127.0.0.1:8000
```

`pipeline.detect` uses YOLO person detection and a centroid tracker to detect entry-threshold crossings. For the full 3-camera setup, run it per clip/camera and replay the combined JSONL output. Zone dwell and staff detection are represented in the event schema so better detectors, Re-ID models, and VLM-based classifiers can be dropped in without touching the API.

## API Endpoints

```bash
# Ingest events
curl -X POST http://127.0.0.1:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{"events":[...]}'

# Analytics
curl "http://127.0.0.1:8000/stores/ST1008/metrics"
curl "http://127.0.0.1:8000/stores/ST1008/funnel"
curl "http://127.0.0.1:8000/stores/ST1008/heatmap"
curl "http://127.0.0.1:8000/stores/ST1008/anomalies"

# Health
curl "http://127.0.0.1:8000/health"
```

All analytics endpoints accept optional `start` and `end` ISO-8601 query parameters. Without them they default to the current UTC day.

## Running Tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Tests cover: idempotent ingestion, partial validation behavior, staff exclusion, re-entry/session deduplication, POS conversion correlation, zero-purchase stores, queue spike anomalies, and dead-zone detection.

## Data Policy

Raw CCTV clips, generated JSONL event files, SQLite databases, and dataset folders are all gitignored. Keep the challenge files in a local path (e.g. `~/Downloads`) and pass them into the scripts at runtime.
