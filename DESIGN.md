# Store Intelligence System — Design

## Overview

The core idea I kept coming back to while building this was a clean production boundary: detection produces immutable behavioral events, and the API turns those events plus POS transactions into store intelligence. That boundary drives every other decision, because it lets the scoring evaluate event quality, API correctness, and operational readiness independently without them stepping on each other.

The runtime splits into four layers. The **detection layer** reads external CCTV clips and emits JSONL events conforming to the challenge schema — right now it's YOLO person detection + a centroid tracker for entry-threshold crossings, but the adapter is intentionally modular so zone dwell, staff detection, and cross-camera Re-ID can all be improved without touching the downstream API. The **event layer** is JSONL + HTTP ingestion: the API validates every event, stores it in SQLite, deduplicates by `event_id`, and returns partial-success responses when some events are malformed rather than rejecting the whole batch. The **analytics layer** computes metrics from events and POS transactions at query time — keeps responses fresh, avoids stale pre-aggregates during challenge replay. The **presentation layer** is a lightweight web dashboard at `/dashboard` that polls the API and shows live metrics as detection or replay feeds events in.

I went with SQLite specifically to keep `docker compose up` simple, deterministic, and easy for reviewers to run. No external dataset or video files in the repo. The POS loader and simulator accept local paths so the challenge files can live outside Git while still enabling a full local demonstration.

## Event Flow

1. `pipeline.detect` processes a CCTV clip and writes JSONL events.
2. `pipeline.simulate_events` can generate validation events from an external POS CSV when clips aren't available.
3. `pipeline.replay_events` sends JSONL events to `POST /events/ingest`.
4. The API validates events with Pydantic, inserts with `INSERT OR IGNORE`, and records duplicates separately.
5. Metrics endpoints query event and POS tables, exclude staff, deduplicate sessions by `visitor_id`, and correlate billing-zone activity to POS transactions within a five-minute lookback window.

## Analytics Semantics

The North Star metric is offline conversion rate. A visitor is counted from customer events — preferring `ENTRY` and `REENTRY` records but falling back to any customer event if a camera misses the entry. A converted visitor is someone who showed up in the billing zone or queue within five minutes before a POS transaction. This mirrors the problem statement exactly and avoids inventing customer identity that the POS data doesn't actually contain.

The funnel tracks visitor sessions, not raw event counts: entry → zone visit → billing queue → purchase. Re-entry doesn't double-count because the same `visitor_id` stays as one session unit. Heatmap responses aggregate visits and dwell by zone and add a low-confidence flag when fewer than 20 sessions exist in the selected window. Anomaly detection covers queue spikes, conversion drops vs. the previous seven-day window, and dead zones that haven't seen any recent visits.

## Production Behavior

The API is containerized and starts with `docker compose up --build`. Every request emits structured logs with trace ID, endpoint, store ID, latency, status code, and event count where relevant. Database errors return structured HTTP 503 responses instead of leaking stack traces. The `/health` endpoint checks database availability, reports last event timestamps by store, and surfaces `STALE_FEED` warnings if a store goes more than ten minutes without new events.

## Where I Used AI Assistance

I leaned on AI in three specific spots and kept the final calls conservative each time.

First, I asked for a comparison of PostgreSQL, Redis streams, and SQLite for a 48-hour hackathon. The AI leaned toward the heavier streaming stack. I overrode that for SQLite because a reliable reviewer startup matters more than infrastructure realism at this stage — and the event boundary still makes Kafka or Redis a clean future addition.

Second, I used it to compare YOLO + ByteTrack, DeepSORT, and centroid tracking. The suggested production path was YOLO + ByteTrack. I picked the centroid tracker for the submitted baseline because it's readable, debuggable, and shows the direction-crossing logic without burying the reasoning inside a large dependency. In a real deployment I'd move to ByteTrack or StrongSORT once clip-level ground truth exists.

Third, I used AI to stress-test the analytics definitions. The most useful thing that came out of that was keeping conversion correlation time-window-based and session-based. I kept that, and I explicitly modeled low-confidence heatmaps, stale feed detection, and partial ingest failures — because these are exactly the edge cases a reviewer is likely to probe.
