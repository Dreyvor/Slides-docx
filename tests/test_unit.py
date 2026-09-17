import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from slides_docx.cli import (
    DEFAULT_CONTACT_SHEET,
    DEFAULT_LEAD,
    DEFAULT_MIN_GAP,
    DEFAULT_THRESHOLD,
    _format_profile_settings,
    _persist_explicit_settings,
    _resolve_command_settings,
    course_date,
    create_parser,
    handle_build,
    handle_detect,
    handle_select,
    main,
    minimum_gap_value,
    time_value,
)
from slides_docx.completion import complete, render_completion
from slides_docx.content import screenshot_time, timestamp_to_seconds, transcript_by_slide
from slides_docx.crop import resolve_crop
from slides_docx.errors import SlidesDocxError
from slides_docx.profiles import (
    ProfileStore,
    format_crop,
    make_profile,
    parse_crop,
    profile_fingerprint,
    profile_settings,
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

    def test_settings_round_trip_partial_update_and_crop_reselection(self):
        self.store.set_profile("lecture", self.profile)
        crop_fingerprint = profile_fingerprint(self.profile)
        self.store.update_settings(
            "lecture", "detect", {"threshold": 14.0, "contact_sheet": False}
        )
        self.store.update_settings("lecture", "detect", {"min_gap": 1.2})
        self.store.update_settings("lecture", "build", {"lead": 2.0})

        _name, saved = self.store.get_profile("lecture")
        self.assertEqual(
            profile_settings(saved, "detect"),
            {"threshold": 14.0, "contact_sheet": False, "min_gap": 1.2},
        )
        self.assertEqual(profile_settings(saved, "build"), {"lead": 2.0})
        self.assertEqual(profile_fingerprint(saved), crop_fingerprint)

        replacement = make_profile((1500, 850, 120, 70), 1920, 1080)
        self.store.set_profile("lecture", replacement)
        _name, replaced = self.store.get_profile("lecture")
        self.assertEqual(profile_settings(replaced, "detect"), profile_settings(saved, "detect"))
        self.assertEqual(profile_settings(replaced, "build"), {"lead": 2.0})
        self.assertNotEqual(profile_fingerprint(replaced), crop_fingerprint)

    def test_invalid_saved_setting_is_rejected(self):
        profile = dict(self.profile)
        profile["settings"] = {"detect": {"threshold": 101}}
        self.store.path.write_text(
            json.dumps({"version": 1, "active_profile": "bad", "profiles": {"bad": profile}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SlidesDocxError, "detect.threshold"):
            self.store.load()

    def test_existing_profile_without_settings_remains_valid(self):
        self.store.set_profile("lecture", self.profile)
        _name, saved = self.store.get_profile("lecture")
        self.assertEqual(profile_settings(saved, "detect"), {})
        self.assertEqual(profile_settings(saved, "build"), {})

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


class ProfileSettingCommandTests(unittest.TestCase):
    def setUp(self):
        self.profile = make_profile((600, 300, 20, 10), 640, 360)
        self.profile["settings"] = {
            "detect": {"threshold": 14.0, "min_gap": 1.25, "contact_sheet": False},
            "build": {"lead": 2.0},
        }
        self.store = Mock()
        self.store.get_profile.return_value = ("room", self.profile)

    def test_explicit_saved_and_builtin_precedence(self):
        resolved = _resolve_command_settings(
            self.store,
            "room",
            "detect",
            {"threshold": 9.0, "min_gap": None, "contact_sheet": None},
            {
                "threshold": DEFAULT_THRESHOLD,
                "min_gap": DEFAULT_MIN_GAP,
                "contact_sheet": DEFAULT_CONTACT_SHEET,
            },
        )
        self.assertEqual(
            resolved,
            {"threshold": 9.0, "min_gap": 1.25, "contact_sheet": False},
        )
        without_profile = _resolve_command_settings(
            self.store,
            None,
            "build",
            {"lead": None},
            {"lead": DEFAULT_LEAD},
        )
        self.assertEqual(without_profile, {"lead": 5.0})

    def test_only_explicit_settings_with_explicit_profile_are_persisted(self):
        _persist_explicit_settings(
            self.store,
            "room",
            "detect",
            {"threshold": 14.0, "min_gap": None, "contact_sheet": False},
        )
        self.store.update_settings.assert_called_once_with(
            "room", "detect", {"threshold": 14.0, "contact_sheet": False}
        )
        self.store.reset_mock()
        _persist_explicit_settings(
            self.store,
            None,
            "detect",
            {"threshold": 9.0, "min_gap": None, "contact_sheet": None},
        )
        self.store.update_settings.assert_not_called()

    def test_parser_uses_tristate_profile_settings(self):
        parser = create_parser()
        omitted = parser.parse_args(["detect", "lecture.mp4"])
        self.assertIsNone(omitted.threshold)
        self.assertIsNone(omitted.min_gap)
        self.assertIsNone(omitted.contact_sheet)
        self.assertTrue(
            parser.parse_args(["detect", "lecture.mp4", "--contact-sheet"]).contact_sheet
        )
        self.assertFalse(
            parser.parse_args(["detect", "lecture.mp4", "--no-contact-sheet"]).contact_sheet
        )
        self.assertIsNone(parser.parse_args(["build", "lecture.mp4", "lecture.vtt"]).lead)

    def test_detect_applies_saved_values_without_persisting_for_active_profile(self):
        args = SimpleNamespace(
            video=Path("lecture.mp4"), output=Path("times.txt"),
            threshold=None, min_gap=None, contact_sheet=None,
            crop=None, profile=None, no_crop=False,
        )
        selection = SimpleNamespace(
            profile_name="room", ffmpeg_value="600:300:20:10", crop=(600, 300, 20, 10)
        )
        with patch("slides_docx.cli.validate_video", return_value=args.video), \
             patch("slides_docx.cli.probe_video", return_value=SimpleNamespace(duration=30, width=640, height=360)), \
             patch("slides_docx.cli._store", return_value=self.store), \
             patch("slides_docx.cli.resolve_crop", return_value=selection), \
             patch("slides_docx.cli.detect_scene_times", return_value=[10.0]) as detect, \
             patch("slides_docx.cli.write_timestamp_file"), \
             patch("slides_docx.cli.create_contact_sheet") as contact:
            self.assertEqual(handle_detect(args), 0)
        detect.assert_called_once_with(args.video, 14.0, "600:300:20:10", 1.25)
        contact.assert_not_called()
        self.store.update_settings.assert_not_called()

    def test_detect_persists_only_after_success(self):
        args = SimpleNamespace(
            video=Path("lecture.mp4"), output=Path("times.txt"),
            threshold=9.0, min_gap=None, contact_sheet=None,
            crop=None, profile="room", no_crop=False,
        )
        selection = SimpleNamespace(
            profile_name="room", ffmpeg_value="600:300:20:10", crop=(600, 300, 20, 10)
        )
        common = (
            patch("slides_docx.cli.validate_video", return_value=args.video),
            patch("slides_docx.cli.probe_video", return_value=SimpleNamespace(duration=30, width=640, height=360)),
            patch("slides_docx.cli._store", return_value=self.store),
            patch("slides_docx.cli.resolve_crop", return_value=selection),
        )
        with common[0], common[1], common[2], common[3], \
             patch("slides_docx.cli.detect_scene_times", side_effect=SlidesDocxError("failed")):
            with self.assertRaisesRegex(SlidesDocxError, "failed"):
                handle_detect(args)
        self.store.update_settings.assert_not_called()

        self.store.reset_mock()
        self.store.get_profile.return_value = ("room", self.profile)
        with patch("slides_docx.cli.validate_video", return_value=args.video), \
             patch("slides_docx.cli.probe_video", return_value=SimpleNamespace(duration=30, width=640, height=360)), \
             patch("slides_docx.cli._store", return_value=self.store), \
             patch("slides_docx.cli.resolve_crop", return_value=selection), \
             patch("slides_docx.cli.detect_scene_times", return_value=[10.0]), \
             patch("slides_docx.cli.write_timestamp_file"):
            self.assertEqual(handle_detect(args), 0)
        self.store.update_settings.assert_called_once_with(
            "room", "detect", {"threshold": 9.0}
        )

    def test_build_uses_timestamp_profile_lead_without_persisting(self):
        args = SimpleNamespace(
            video=Path("lecture.mp4"), vtt=Path("lecture.vtt"),
            slide_times=Path("times.txt"), output=Path("lecture.docx"),
            lead=None, date=None, crop=None, profile=None, no_crop=False,
        )
        selection = SimpleNamespace(
            profile_name="room", ffmpeg_value="600:300:20:10", crop=(600, 300, 20, 10)
        )
        with patch("slides_docx.cli.validate_video", return_value=args.video), \
             patch("slides_docx.cli.probe_video", return_value=SimpleNamespace(duration=30, width=640, height=360)), \
             patch("slides_docx.cli.read_timestamp_file", return_value=({}, [10.0])), \
             patch("slides_docx.cli._store", return_value=self.store), \
             patch("slides_docx.cli.resolve_crop", return_value=selection), \
             patch("slides_docx.cli.build_document") as build:
            self.assertEqual(handle_build(args), 0)
        self.assertEqual(build.call_args.kwargs["lead"], 2.0)
        self.store.update_settings.assert_not_called()

    def test_build_persists_explicit_lead_only_after_success(self):
        args = SimpleNamespace(
            video=Path("lecture.mp4"), vtt=Path("lecture.vtt"),
            slide_times=Path("times.txt"), output=Path("lecture.docx"),
            lead=3.0, date=None, crop=None, profile="room", no_crop=False,
        )
        selection = SimpleNamespace(
            profile_name="room", ffmpeg_value="600:300:20:10", crop=(600, 300, 20, 10)
        )

        def run_with_build(build):
            with patch("slides_docx.cli.validate_video", return_value=args.video), \
                 patch("slides_docx.cli.probe_video", return_value=SimpleNamespace(duration=30, width=640, height=360)), \
                 patch("slides_docx.cli.read_timestamp_file", return_value=({}, [10.0])), \
                 patch("slides_docx.cli._store", return_value=self.store), \
                 patch("slides_docx.cli.resolve_crop", return_value=selection), \
                 patch("slides_docx.cli.build_document", build):
                return handle_build(args)

        with self.assertRaisesRegex(SlidesDocxError, "failed"):
            run_with_build(Mock(side_effect=SlidesDocxError("failed")))
        self.store.update_settings.assert_not_called()

        self.store.reset_mock()
        self.store.get_profile.return_value = ("room", self.profile)
        self.assertEqual(run_with_build(Mock()), 0)
        self.store.update_settings.assert_called_once_with(
            "room", "build", {"lead": 3.0}
        )

    def test_profile_listing_formats_saved_settings(self):
        self.assertEqual(
            _format_profile_settings(self.profile),
            "; settings: threshold=14, min-gap=1.25, contact-sheet=no, lead=2",
        )


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.parser = create_parser()

    def test_commands_options_and_choices_are_completed_from_parser(self):
        self.assertEqual(
            complete(self.parser, ["slides-docx", "de"], 1),
            ["detect"],
        )
        options = complete(
            self.parser,
            ["slides-docx", "detect", "lecture.mp4", "--"],
            3,
        )
        self.assertIn("--threshold", options)
        self.assertIn("--profile", options)
        self.assertEqual(
            complete(self.parser, ["slides-docx", "completion", "po"], 2),
            ["powershell"],
        )

    def test_used_and_mutually_exclusive_options_are_hidden(self):
        options = complete(
            self.parser,
            [
                "slides-docx", "detect", "lecture.mp4", "--threshold", "10",
                "--profile", "room", "--",
            ],
            7,
        )
        self.assertNotIn("--threshold", options)
        self.assertNotIn("--profile", options)
        self.assertNotIn("--crop", options)
        self.assertNotIn("--no-crop", options)
        self.assertIn("--min-gap", options)

    def test_saved_profiles_are_completed_for_options_and_actions(self):
        def names():
            return ["room", "Lecture Hall", "université"]

        self.assertEqual(
            complete(
                self.parser,
                ["slides-docx", "detect", "lecture.mp4", "--profile", "uni"],
                4,
                profile_names=names,
            ),
            ["université"],
        )
        self.assertEqual(
            complete(
                self.parser,
                ["slides-docx", "profiles", "delete", "Lec"],
                3,
                profile_names=names,
            ),
            ["Lecture Hall"],
        )

    def test_file_completion_handles_spaces_unicode_and_expected_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "course files"
            folder.mkdir()
            (folder / "lécture.vtt").touch()
            (folder / "lécture.txt").touch()
            candidates = complete(
                self.parser,
                ["slides-docx", "build", "video.mp4", "course files/lé"],
                3,
                cwd=root,
            )
            self.assertEqual(candidates, [str(Path("course files") / "lécture.vtt")])

    def test_completion_errors_are_silent(self):
        def broken_profiles():
            raise SlidesDocxError("broken configuration")

        self.assertEqual(
            complete(
                self.parser,
                ["slides-docx", "select", "video.mp4", "--profile", ""],
                4,
                profile_names=broken_profiles,
            ),
            [],
        )

    def test_shell_scripts_register_slides_docx(self):
        registrations = {
            "bash": "complete -F _slides_docx_complete slides-docx",
            "zsh": "compdef _slides_docx_complete slides-docx",
            "fish": "complete -c slides-docx",
            "powershell": "Register-ArgumentCompleter -Native -CommandName slides-docx",
        }
        for shell, registration in registrations.items():
            with self.subTest(shell=shell):
                script = render_completion(shell)
                self.assertIn(registration, script)
                self.assertIn("slides-docx _complete", script)

    def test_available_shell_scripts_have_valid_syntax(self):
        for shell in ("bash", "zsh"):
            executable = shutil.which(shell)
            if executable is None:
                continue
            with self.subTest(shell=shell):
                result = subprocess.run(
                    [executable, "-n"],
                    input=render_completion(shell),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_internal_completion_does_not_probe_video(self):
        output = io.StringIO()
        with patch("slides_docx.cli.probe_video") as probe, \
             patch("slides_docx.cli.detect_scene_times") as detect, \
             contextlib.redirect_stdout(output):
            self.assertEqual(main(["_complete", "1", "slides-docx", "d"]), 0)
        self.assertEqual(output.getvalue(), "detect\n")
        probe.assert_not_called()
        detect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
