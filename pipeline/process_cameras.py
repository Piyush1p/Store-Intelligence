from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.detect import load_zones, run_video
from pipeline.emit import read_jsonl, write_jsonl


def escape_invalid_json_backslashes(text: str) -> str:
    """Recover common Windows paths with unescaped backslashes inside JSON strings."""
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    text = manifest_path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        repaired = escape_invalid_json_backslashes(text)
        if repaired != text:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        raise ValueError(
            "Invalid manifest JSON. If you used a Windows path, write it with "
            "forward slashes like C:/path/to/CCTV Footage or "
            "escape backslashes as C:\\\\path\\\\to\\\\CCTV Footage."
        ) from exc


def parse_started_at(value: str | None) -> datetime:
    if value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def resolve_path(value: str, base_dir: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute() or base_dir is None:
        return path
    return base_dir / path


def camera_zones(camera: dict[str, Any], manifest_dir: Path) -> list[dict[str, Any]]:
    if "zones" in camera:
        return camera["zones"]
    zones_json = camera.get("zones_json")
    if zones_json:
        return load_zones(str(resolve_path(zones_json, manifest_dir)))
    return []


def process_manifest(manifest_path: Path, output_path: Path) -> int:
    manifest = load_manifest(manifest_path)
    manifest_dir = manifest_path.parent
    video_dir = resolve_path(manifest.get("video_dir", "."), manifest_dir)
    store_id = manifest["store_id"]
    default_started_at = parse_started_at(manifest.get("started_at"))
    default_model = manifest.get("model", "yolov8n.pt")
    default_frame_stride = int(manifest.get("frame_stride", 3))
    intermediate_dir = output_path.parent / "camera_events"
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    all_events = []
    for camera in manifest.get("cameras", []):
        if camera.get("enabled", True) is False:
            continue
        file_value = camera.get("path") or camera.get("file")
        if not file_value:
            raise ValueError(f"camera entry missing path/file: {camera}")
        video_path = resolve_path(file_value, video_dir)
        camera_id = camera["camera_id"]
        camera_output = intermediate_dir / f"{camera_id}.jsonl"
        run_video(
            video_path=video_path,
            output_path=camera_output,
            store_id=store_id,
            camera_id=camera_id,
            model_name=camera.get("model", default_model),
            mode=camera.get("mode", "entry"),
            threshold_axis=camera.get("threshold_axis", "x"),
            threshold_ratio=float(camera.get("threshold_ratio", 0.5)),
            outside_side=camera.get("outside_side", "low"),
            zones=camera_zones(camera, manifest_dir),
            dwell_seconds=int(camera.get("dwell_seconds", manifest.get("dwell_seconds", 30))),
            started_at=parse_started_at(camera.get("started_at")) if camera.get("started_at") else default_started_at,
            frame_stride=int(camera.get("frame_stride", default_frame_stride)),
        )
        all_events.extend(read_jsonl(camera_output))

    all_events.sort(key=lambda event: (event["timestamp"], event["camera_id"], event["visitor_id"]))
    return write_jsonl(all_events, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Process multiple camera clips from a manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", default="data/detected_events.jsonl")
    args = parser.parse_args()

    count = process_manifest(Path(args.manifest), Path(args.output))
    print(f"wrote {count} merged events to {args.output}")


if __name__ == "__main__":
    main()
