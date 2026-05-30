# PROMPT: Add tests for detection helper logic after calibrating the supplied CCTV
# sample clips, especially entry direction when the outside side is on the right.
# CHANGES MADE: Kept tests dependency-free by checking pure threshold and polygon helpers.

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline.detect import event_type_for_transition, point_in_polygon, side_of_threshold
from pipeline.process_cameras import load_manifest


class DetectionHelperTests(unittest.TestCase):
    def test_entry_direction_can_be_right_to_left_for_cam3(self):
        self.assertEqual(side_of_threshold((1200, 500), axis="x", threshold_px=998), "high")
        self.assertEqual(side_of_threshold((800, 500), axis="x", threshold_px=998), "low")
        self.assertEqual(event_type_for_transition("high", "low", outside_side="high"), "ENTRY")
        self.assertEqual(event_type_for_transition("low", "high", outside_side="high"), "EXIT")

    def test_normalized_zone_polygon(self):
        polygon = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]]
        self.assertTrue(point_in_polygon((500, 500), polygon, width=1000, height=1000))
        self.assertFalse(point_in_polygon((100, 500), polygon, width=1000, height=1000))

    def test_manifest_loader_recovers_common_windows_path_backslashes(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(
                r'{"store_id":"ST1008","video_dir":"C:\dataset\CCTV Footage","cameras":[]}',
                encoding="utf-8",
            )
            manifest = load_manifest(path)
            self.assertEqual(manifest["video_dir"], r"C:\dataset\CCTV Footage")


if __name__ == "__main__":
    unittest.main()
