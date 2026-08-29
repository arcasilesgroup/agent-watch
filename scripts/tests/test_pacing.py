"""Unit tests for pacing math."""
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from pacing import compute_pacing  # noqa: E402


class TestPacing(unittest.TestCase):

    def test_basic_metrics(self):
        scene_times = [0.0, 5.0, 13.0, 25.0, 35.0, 50.0]
        result = compute_pacing(
            scene_times=scene_times,
            video_duration=60.0,
            motion_scores=None,
        )
        self.assertEqual(result["shot_count"], 6)
        self.assertAlmostEqual(result["cuts_per_minute"], 6.0, places=1)
        self.assertAlmostEqual(result["mean_shot_length"], 10.0, places=1)
        self.assertEqual(len(result["shots"]), 6)
        self.assertAlmostEqual(result["shots"][0]["start_seconds"], 0.0)
        self.assertAlmostEqual(result["shots"][0]["duration_seconds"], 5.0)
        self.assertAlmostEqual(result["shots"][-1]["start_seconds"], 50.0)
        self.assertAlmostEqual(result["shots"][-1]["duration_seconds"], 10.0)

    def test_handles_single_shot(self):
        result = compute_pacing(
            scene_times=[0.0],
            video_duration=120.0,
            motion_scores=None,
        )
        self.assertEqual(result["shot_count"], 1)
        self.assertAlmostEqual(result["cuts_per_minute"], 0.5, places=1)
        self.assertAlmostEqual(result["mean_shot_length"], 120.0)

    def test_handles_empty_input(self):
        result = compute_pacing(scene_times=[], video_duration=60.0, motion_scores=None)
        self.assertEqual(result["shot_count"], 0)
        self.assertEqual(result["cuts_per_minute"], 0.0)
        self.assertEqual(result["mean_shot_length"], 0.0)
        self.assertEqual(result["shots"], [])

    def test_motion_scores_attached(self):
        result = compute_pacing(
            scene_times=[0.0, 10.0, 20.0],
            video_duration=30.0,
            motion_scores=[0.1, 0.5, 0.9],
        )
        scores = [s["motion_score"] for s in result["shots"]]
        self.assertEqual(scores, [0.1, 0.5, 0.9])


if __name__ == "__main__":
    unittest.main()
