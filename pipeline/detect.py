from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.emit import make_event, write_jsonl


@dataclass
class Track:
    track_id: int
    centroid: tuple[int, int]
    first_seen: datetime
    last_seen: datetime
    confidence: float = 0.0
    last_side: str | None = None
    emitted_entry: bool = False
    emitted_exit: bool = False
    active_zones: dict[str, datetime] = field(default_factory=dict)
    last_dwell_emit: dict[str, datetime] = field(default_factory=dict)


class CentroidTracker:
    def __init__(self, max_distance: int = 90, max_missing_frames: int = 20) -> None:
        self.max_distance = max_distance
        self.max_missing_frames = max_missing_frames
        self.next_id = 1
        self.tracks: OrderedDict[int, Track] = OrderedDict()
        self.missing: dict[int, int] = {}

    def update(self, detections: list[tuple[int, int, float]], timestamp: datetime) -> list[Track]:
        if not self.tracks:
            for x, y, confidence in detections:
                self._register((x, y), confidence, timestamp)
            return list(self.tracks.values())

        unmatched = set(range(len(detections)))
        for track_id, track in list(self.tracks.items()):
            best_index = None
            best_distance = float("inf")
            for index in unmatched:
                x, y, _ = detections[index]
                distance = ((track.centroid[0] - x) ** 2 + (track.centroid[1] - y) ** 2) ** 0.5
                if distance < best_distance:
                    best_index = index
                    best_distance = distance
            if best_index is not None and best_distance <= self.max_distance:
                x, y, confidence = detections[best_index]
                track.centroid = (x, y)
                track.confidence = confidence
                track.last_seen = timestamp
                self.missing[track_id] = 0
                unmatched.remove(best_index)
            else:
                self.missing[track_id] = self.missing.get(track_id, 0) + 1

        for index in unmatched:
            x, y, confidence = detections[index]
            self._register((x, y), confidence, timestamp)

        for track_id, missing in list(self.missing.items()):
            if missing > self.max_missing_frames:
                self.tracks.pop(track_id, None)
                self.missing.pop(track_id, None)
        return [track for track_id, track in self.tracks.items() if self.missing.get(track_id, 0) == 0]

    def _register(self, centroid: tuple[int, int], confidence: float, timestamp: datetime) -> None:
        track = Track(
            track_id=self.next_id,
            centroid=centroid,
            first_seen=timestamp,
            last_seen=timestamp,
            confidence=confidence,
        )
        self.tracks[self.next_id] = track
        self.missing[self.next_id] = 0
        self.next_id += 1


def load_yolo_detector(model_name: str):
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: ultralytics. Install project dependencies with "
            "`python -m pip install -r requirements.txt`. If you are on Windows, "
            "Python 3.12 is the safest local runtime for the video pipeline."
        ) from exc

    model = YOLO(model_name)

    def detect(frame):
        result = model.predict(frame, classes=[0], conf=0.25, verbose=False)[0]
        boxes = []
        for box in result.boxes:
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
            confidence = float(box.conf[0])
            boxes.append((x1, y1, x2, y2, confidence))
        return boxes

    return detect


def side_of_threshold(
    centroid: tuple[int, int],
    *,
    axis: str,
    threshold_px: int,
) -> str:
    value = centroid[0] if axis == "x" else centroid[1]
    return "low" if value < threshold_px else "high"


def event_type_for_transition(previous_side: str, side: str, outside_side: str) -> str | None:
    inside_side = "high" if outside_side == "low" else "low"
    if previous_side == outside_side and side == inside_side:
        return "ENTRY"
    if previous_side == inside_side and side == outside_side:
        return "EXIT"
    return None


def point_in_polygon(point: tuple[int, int], polygon: list[list[float]], width: int, height: int) -> bool:
    x, y = point
    scaled = [
        (px * width if 0 <= px <= 1 else px, py * height if 0 <= py <= 1 else py)
        for px, py in polygon
    ]
    inside = False
    j = len(scaled) - 1
    for i, (xi, yi) in enumerate(scaled):
        xj, yj = scaled[j]
        intersects = (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        if intersects:
            inside = not inside
        j = i
    return inside


def zones_for_point(
    point: tuple[int, int],
    zones: list[dict[str, Any]],
    width: int,
    height: int,
) -> set[str]:
    matched = set()
    for zone in zones:
        polygon = zone.get("polygon")
        zone_id = zone.get("zone_id")
        if zone_id and polygon and point_in_polygon(point, polygon, width, height):
            matched.add(zone_id)
    return matched


def emit_entry_exit_events(
    *,
    tracks: list[Track],
    timestamp: datetime,
    events: list[dict[str, Any]],
    store_id: str,
    camera_id: str,
    threshold_axis: str,
    threshold_px: int,
    outside_side: str,
) -> None:
    for track in tracks:
        side = side_of_threshold(track.centroid, axis=threshold_axis, threshold_px=threshold_px)
        if track.last_side and track.last_side != side:
            event_type = event_type_for_transition(track.last_side, side, outside_side)
            visitor_id = f"VIS_{track.track_id:06d}"
            if event_type == "ENTRY" and not track.emitted_entry:
                events.append(
                    make_event(
                        store_id=store_id,
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="ENTRY",
                        timestamp=timestamp,
                        confidence=track.confidence or 0.72,
                        metadata={
                            "tracker": "centroid_yolo",
                            "threshold_axis": threshold_axis,
                            "threshold_px": threshold_px,
                            "outside_side": outside_side,
                        },
                    )
                )
                track.emitted_entry = True
            elif event_type == "EXIT" and not track.emitted_exit:
                events.append(
                    make_event(
                        store_id=store_id,
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="EXIT",
                        timestamp=timestamp,
                        confidence=track.confidence or 0.72,
                        metadata={
                            "tracker": "centroid_yolo",
                            "threshold_axis": threshold_axis,
                            "threshold_px": threshold_px,
                            "outside_side": outside_side,
                        },
                    )
                )
                track.emitted_exit = True
        track.last_side = side


def emit_zone_events(
    *,
    tracks: list[Track],
    timestamp: datetime,
    events: list[dict[str, Any]],
    store_id: str,
    camera_id: str,
    zones: list[dict[str, Any]],
    width: int,
    height: int,
    dwell_seconds: int,
) -> None:
    dwell_interval = timedelta(seconds=dwell_seconds)
    for track in tracks:
        visitor_id = f"VIS_{track.track_id:06d}"
        current_zones = zones_for_point(track.centroid, zones, width, height)

        for zone_id in sorted(current_zones - set(track.active_zones)):
            track.active_zones[zone_id] = timestamp
            events.append(
                make_event(
                    store_id=store_id,
                    camera_id=camera_id,
                    visitor_id=visitor_id,
                    event_type="ZONE_ENTER",
                    timestamp=timestamp,
                    zone_id=zone_id,
                    confidence=track.confidence or 0.72,
                    metadata={"tracker": "centroid_yolo", "session_seq": len(track.active_zones)},
                )
            )

        for zone_id in sorted(current_zones):
            entered_at = track.active_zones.get(zone_id, timestamp)
            last_emit = track.last_dwell_emit.get(zone_id, entered_at)
            if timestamp - entered_at >= dwell_interval and timestamp - last_emit >= dwell_interval:
                dwell_ms = int((timestamp - entered_at).total_seconds() * 1000)
                events.append(
                    make_event(
                        store_id=store_id,
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="ZONE_DWELL",
                        timestamp=timestamp,
                        zone_id=zone_id,
                        dwell_ms=dwell_ms,
                        confidence=track.confidence or 0.72,
                        metadata={"tracker": "centroid_yolo"},
                    )
                )
                track.last_dwell_emit[zone_id] = timestamp

        for zone_id in sorted(set(track.active_zones) - current_zones):
            entered_at = track.active_zones.pop(zone_id)
            track.last_dwell_emit.pop(zone_id, None)
            dwell_ms = int((timestamp - entered_at).total_seconds() * 1000)
            events.append(
                make_event(
                    store_id=store_id,
                    camera_id=camera_id,
                    visitor_id=visitor_id,
                    event_type="ZONE_EXIT",
                    timestamp=timestamp,
                    zone_id=zone_id,
                    dwell_ms=max(0, dwell_ms),
                    confidence=track.confidence or 0.72,
                    metadata={"tracker": "centroid_yolo"},
                )
            )


def load_zones(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload.get("zones", [])
    return payload


def run_video(
    *,
    video_path: Path,
    output_path: Path,
    store_id: str,
    camera_id: str,
    model_name: str,
    mode: str,
    threshold_axis: str,
    threshold_ratio: float,
    outside_side: str,
    zones: list[dict[str, Any]] | None,
    dwell_seconds: int,
    started_at: datetime,
    frame_stride: int,
) -> int:
    try:
        import cv2
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: opencv-python-headless. Install project dependencies with "
            "`python -m pip install -r requirements.txt`. If you are using Microsoft Store "
            "Python 3.13, switch to Python 3.12 or run via Docker because CV/YOLO wheels "
            "are often unavailable or inconsistent on 3.13."
        ) from exc

    detector = load_yolo_detector(model_name)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise SystemExit(f"could not open video: {video_path}")

    fps = capture.get(cv2.CAP_PROP_FPS) or 15
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    threshold_px = int((width if threshold_axis == "x" else height) * threshold_ratio)
    tracker = CentroidTracker()
    events: list[dict[str, Any]] = []
    frame_no = 0
    zones = zones or []

    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frame_no += 1
        if frame_no % frame_stride != 0:
            continue
        timestamp = started_at + timedelta(seconds=frame_no / fps)
        boxes = detector(frame)
        detections = [((x1 + x2) // 2, (y1 + y2) // 2, confidence) for x1, y1, x2, y2, confidence in boxes]
        tracks = tracker.update(detections, timestamp)

        if mode in {"entry", "both"}:
            emit_entry_exit_events(
                tracks=tracks,
                timestamp=timestamp,
                events=events,
                store_id=store_id,
                camera_id=camera_id,
                threshold_axis=threshold_axis,
                threshold_px=threshold_px,
                outside_side=outside_side,
            )
        if mode in {"zone", "both"} and zones:
            emit_zone_events(
                tracks=tracks,
                timestamp=timestamp,
                events=events,
                store_id=store_id,
                camera_id=camera_id,
                zones=zones,
                width=width,
                height=height,
                dwell_seconds=dwell_seconds,
            )

    capture.release()
    return write_jsonl(events, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Process an external CCTV clip into challenge events.")
    parser.add_argument("--video", required=True, help="Path to raw CCTV clip. Do not commit this file.")
    parser.add_argument("--output", default="data/detected_events.jsonl")
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--camera-id", default="CAM_ENTRY_01")
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--mode", choices=["entry", "zone", "both"], default="entry")
    parser.add_argument("--threshold-axis", choices=["x", "y"], default="x")
    parser.add_argument("--threshold-ratio", type=float, default=0.5)
    parser.add_argument("--outside-side", choices=["low", "high"], default="low")
    parser.add_argument("--zones-json", default=None, help="JSON file containing zones with normalized polygons.")
    parser.add_argument("--dwell-seconds", type=int, default=30)
    parser.add_argument("--started-at", default=None, help="ISO UTC start timestamp for frame zero.")
    parser.add_argument("--frame-stride", type=int, default=3)
    args = parser.parse_args()

    started_at = (
        datetime.fromisoformat(args.started_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        if args.started_at
        else datetime.now(timezone.utc)
    )
    count = run_video(
        video_path=Path(args.video),
        output_path=Path(args.output),
        store_id=args.store_id,
        camera_id=args.camera_id,
        model_name=args.model,
        mode=args.mode,
        threshold_axis=args.threshold_axis,
        threshold_ratio=args.threshold_ratio,
        outside_side=args.outside_side,
        zones=load_zones(args.zones_json),
        dwell_seconds=args.dwell_seconds,
        started_at=started_at,
        frame_stride=args.frame_stride,
    )
    print(f"wrote {count} events to {args.output}")


if __name__ == "__main__":
    main()
