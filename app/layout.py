from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_layout(path: str | None = None) -> dict[str, Any]:
    candidate = path or os.getenv("STORE_LAYOUT_PATH")
    if not candidate:
        return {"stores": []}
    layout_path = Path(candidate)
    if not layout_path.exists():
        return {"stores": []}
    return json.loads(layout_path.read_text(encoding="utf-8"))


def zones_for_store(store_id: str, path: str | None = None) -> set[str]:
    layout = load_layout(path)
    for store in layout.get("stores", []):
        if store.get("store_id") == store_id:
            return {zone.get("zone_id") for zone in store.get("zones", []) if zone.get("zone_id")}
    return set()

