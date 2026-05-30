# Engineering Choices

## 1. Detection Model

I looked at YOLOv8/YOLOv9 with a tracker, RT-DETR, MediaPipe, and a pure motion-detection baseline. After thinking through the trade-offs, YOLO + ByteTrack felt like the strongest production pick — great person-detection support, widely battle-tested, and handles modest CCTV quality without complaint. But for the submitted baseline I went with YOLO + a simple centroid tracker, and I kept the interface modular so swapping the tracker out later is painless.

The reasoning is pretty straightforward: the challenge clips weren't included in the shared files, and the scoring rewards working systems with defensible trade-offs over raw model complexity. A centroid tracker makes the important logic completely transparent — detect person boxes, update track centroids, compare movement across an entry threshold, emit `ENTRY` or `EXIT` events with confidence. It's not what I'd ship to a crowded production billing scene, but it's a clean, fast-to-validate baseline. For production, I'd upgrade the adapter to ByteTrack for short-term occlusion handling and layer in an appearance-based Re-ID model like OSNet for cross-camera re-entry.

For staff classification, the schema already carries `is_staff` and metadata fields. In a real deployment I'd start with uniform-color heuristics and camera-zone rules, then bring in a VLM only on low-confidence crops — not on every frame, because the cost, latency, and privacy exposure just aren't worth it.

## 2. Event Schema

I followed the problem statement schema closely: globally unique `event_id`, `store_id`, `camera_id`, `visitor_id`, event type, timestamp, zone, dwell, staff flag, confidence, and metadata. I considered adding separate session IDs and detection IDs, but rejected that for the public API — the challenge already treats `visitor_id` as the session token, and tacking on extra required fields would make schema validation unnecessarily brittle at scoring time.

Enrichments go into `metadata` instead: queue depth, source, tracker name, threshold location, session sequence. That keeps the API contract stable while still giving operators enough context to debug weird counts in production. I also made a deliberate choice not to suppress low-confidence events — they're ingested with their actual confidence scores so downstream metrics surface uncertainty rather than quietly swallowing hard frames.

The schema is intentionally immutable. Once an event is written, corrections come in as new events or future enrichment jobs, never as in-place mutations. That makes ingestion idempotent and replay completely safe.

## 3. API Architecture

I weighed FastAPI + SQLite, FastAPI + PostgreSQL, and a streaming design with Kafka or Redis. PostgreSQL + Redis streams is the right production answer — but the first thing a reviewer does is `docker compose up`, and a single-container service is the least fragile way to pass that gate.

SQLite isn't a cop-out here. The scoring dataset is small, the API is mostly windowed reads, and the schema has indexes on store and timestamp. Idempotency is handled by a primary key on `event_id`, which keeps duplicate ingestion simple and reliable. If this scaled to 40 stores in real time, the first migration would be PostgreSQL for concurrent writes and retention, followed by a queue between detection and ingestion so camera backpressure doesn't silently drop events.

Analytics are computed at read time. Pre-aggregations would be faster, but they introduce cache invalidation risk and can hide bugs that a replay would otherwise expose. Query-time computation keeps the logic inspectable, easy to test, and correct after replays — which matters a lot when you're being scored on correctness.
