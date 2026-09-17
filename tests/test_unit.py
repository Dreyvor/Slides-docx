import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from slides_docx.cli import course_date, handle_select, minimum_gap_value, time_value
from slides_docx.content import screenshot_time, timestamp_to_seconds, transcript_by_slide
from slides_docx.crop import resolve_crop
from slides_docx.errors import SlidesDocxError
from slides_docx.profiles import (
    ProfileStore,
    format_crop,
    make_profile,
    parse_crop,
    profile_fingerprint,
    scale_profile,
)
from slides_docx.timestamps import read_timestamp_file, write_timestamp_file
from slides_docx.video import merge_rapid_scene_changes


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ProfileStore(Path(self.temporary.name) / "config.json")
        self.profile = make_profile((1600, 900, 100, 50), 1920, 1080)

    def tearDown(self):
        self.temporary.cleanup()

    def test_profile_round_trip_and_activation(self):
        self.store.set_profile("lecture", self.profile)
        name, profile = self.store.get_profile()
        self.assertEqual(name, "lecture")
        self.assertEqual(profile["width"], 1600)
        self.assertEqual(json.loads(self.store.path.read_text())["version"], 1)

    def test_profile_scales_for_matching_aspect_ratio(self):
        self.assertEqual(scale_profile(self.profile, 1280, 720), (1067, 600, 67, 33))

    def test_profile_rejects_different_aspect_ratio(self):
        with self.assertRaisesRegex(SlidesDocxError, "different aspect ratio"):
            scale_profile(self.profile, 1024, 768)

    def test_delete_active_profile_is_refused(self):
        self.store.set_profile("lecture", self.profile)
        with self.assertRaisesRegex(SlidesDocxError, "active profile"):
            self.store.delete("lecture")

    def test_crop_validation(self):
        self.assertEqual(parse_crop("1600:900:100:50"), (1600, 900, 100, 50))
        self.assertEqual(format_crop((1600, 900, 100, 50)), "1600:900:100:50")
        for value in ("bad", "0:10:0:0", "10:10:-1:0"):
            with self.subTest(value=value), self.assertRaises(SlidesDocxError):
                parse_crop(value)

    def test_crop_precedence_and_fingerprint(self):
        self.store.set_profile("lecture", self.profile)
        explicit = resolve_crop(self.store, 1920, 1080, explicit_crop="10:20:1:2")
        self.assertEqual(explicit.crop, (10, 20, 1, 2))
        self.assertEqual(explicit.mode, "explicit")
        selected = resolve_crop(self.store, 1920, 1080)
        self.assertEqual(selected.profile_name, "lecture")
        self.assertEqual(selected.fingerprint, profile_fingerprint(self.profile))
        self.assertIsNone(resolve_crop(self.store, 1920, 1080, no_crop=True).crop)

    def test_changed_referenced_profile_is_rejected(self):
        self.store.set_profile("lecture", self.profile)
        metadata = {
            "crop-mode": "profile",
            "crop-profile": "lecture",
            "crop-fingerprint": "changed",
        }
        with self.assertRaisesRegex(SlidesDocxError, "changed after slide detection"):
            resolve_crop(self.store, 1920, 1080, timestamp_metadata=metadata)
        override = resolve_crop(
            self.store, 1920, 1080, profile_name="lecture", timestamp_metadata=metadata
        )
        self.assertEqual(override.profile_name, "lecture")


class TimestampFileTests(unittest.TestCase):
    def test_metadata_and_times_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lecture.slide-times.txt"
            store = ProfileStore(Path(directory) / "config.json")
            profile = make_profile((800, 600, 0, 0), 1024, 768)
            store.set_profile("room", profile)
            selection = resolve_crop(store, 1024, 768)
            write_timestamp_file(path, [35.24, 10.0, 35.24], selection)
            metadata, times = read_timestamp_file(path, 100)
            self.assertEqual(times, [10.0, 35.24])
            self.assertEqual(metadata["crop-profile"], "room")
            self.assertEqual(metadata["crop-fingerprint"], profile_fingerprint(profile))


class ContentTests(unittest.TestCase):
    def test_timestamp_formats(self):
        self.assertEqual(timestamp_to_seconds("12.5"), 12.5)
        self.assertEqual(timestamp_to_seconds("01:02.500"), 62.5)
        self.assertEqual(timestamp_to_seconds("01:02:03.500"), 3723.5)
        self.assertEqual(time_value("00:30"), 30)
        for value in ("1:60", "-1", "nan", "inf", "bad"):
            with self.subTest(value=value), self.assertRaises((ValueError, argparse.ArgumentTypeError)):
                timestamp_to_seconds(value)

    def test_transcript_assignment_and_short_slide_capture(self):
        self.assertEqual(
            transcript_by_slide([(1, "a"), (11, "b")], [0, 10]), [["a"], ["b"]]
        )
        self.assertAlmostEqual(screenshot_time(0, 2, 5), 1.5)

    def test_course_date(self):
        self.assertEqual(course_date("29.02.2024").strftime("%Y_%m_%d"), "2024_02_29")
        with self.assertRaises(argparse.ArgumentTypeError):
            course_date("29.02.2025")

    def test_rapid_scene_changes_are_merged(self):
        self.assertEqual(
            merge_rapid_scene_changes([20.0, 10.7, 10.0, 10.2, 20.8]),
            [10.0, 20.0, 20.8],
        )
        self.assertEqual(merge_rapid_scene_changes([]), [])
        self.assertEqual(
            merge_rapid_scene_changes([1.0, 1.1, 1.2], minimum_gap=0),
            [1.0, 1.1, 1.2],
        )
        self.assertEqual(minimum_gap_value("1.25"), 1.25)
        for value in ("-0.1", "nan", "bad"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                minimum_gap_value(value)


class SelectorTests(unittest.TestCase):
    def test_selector_saves_profile_and_clamps_preview_time(self):
        fake_cv2 = SimpleNamespace(
            imread=Mock(return_value=object()),
            selectROI=Mock(return_value=(10, 20, 300, 200)),
            destroyAllWindows=Mock(),
        )
        store = Mock()
        arguments = SimpleNamespace(video=Path("lecture.mp4"), profile="room", at=30.0)
        with patch.dict(sys.modules, {"cv2": fake_cv2}), \
             patch("slides_docx.cli.validate_video", return_value=arguments.video), \
             patch("slides_docx.cli.probe_video", return_value=SimpleNamespace(duration=10, width=640, height=360)), \
             patch("slides_docx.cli.extract_frame") as extract, \
             patch("slides_docx.cli._store", return_value=store):
            self.assertEqual(handle_select(arguments), 0)
        self.assertAlmostEqual(extract.call_args.args[1], 9.85)
        saved = store.set_profile.call_args.args[1]
        self.assertEqual((saved["width"], saved["height"], saved["x"], saved["y"]), (300, 200, 10, 20))

    def test_cancel_does_not_change_profile(self):
        fake_cv2 = SimpleNamespace(
            imread=Mock(return_value=object()),
            selectROI=Mock(return_value=(0, 0, 0, 0)),
            destroyAllWindows=Mock(),
        )
        store = Mock()
        arguments = SimpleNamespace(video=Path("lecture.mp4"), profile="default", at=1.0)
        with patch.dict(sys.modules, {"cv2": fake_cv2}), \
             patch("slides_docx.cli.validate_video", return_value=arguments.video), \
             patch("slides_docx.cli.probe_video", return_value=SimpleNamespace(duration=10, width=640, height=360)), \
             patch("slides_docx.cli.extract_frame"), \
             patch("slides_docx.cli._store", return_value=store):
            self.assertEqual(handle_select(arguments), 0)
        store.set_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
