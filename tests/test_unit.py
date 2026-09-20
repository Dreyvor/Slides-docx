import argparse
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from slides_docx.cli import (
    course_date,
    create_parser,
    main,
    minimum_gap_value,
    time_value,
)
from slides_docx.completion import complete, render_completion
from slides_docx.content import screenshot_time, timestamp_to_seconds, transcript_by_slide
from slides_docx.crop import resolve_crop
from slides_docx.errors import JobCancelledError, SlidesDocxError
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
from slides_docx.crop import CropSelection
from slides_docx.services import (
    BuildRequest,
    CancellationToken,
    DetectRequest,
    PreviewRequest,
    build_docx,
    detect_slides,
    ensure_output_suffix,
    extract_preview,
)
from slides_docx.video import VideoInfo, merge_rapid_scene_changes, resolve_tool


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
        self.store.update_settings(
            "lecture", "build", {"lead": 2.0, "slide_images": False}
        )

        _name, saved = self.store.get_profile("lecture")
        self.assertEqual(
            profile_settings(saved, "detect"),
            {"threshold": 14.0, "contact_sheet": False, "min_gap": 1.2},
        )
        self.assertEqual(
            profile_settings(saved, "build"),
            {"lead": 2.0, "slide_images": False},
        )
        self.assertEqual(profile_fingerprint(saved), crop_fingerprint)

        replacement = make_profile((1500, 850, 120, 70), 1920, 1080)
        self.store.set_profile("lecture", replacement)
        _name, replaced = self.store.get_profile("lecture")
        self.assertEqual(profile_settings(replaced, "detect"), profile_settings(saved, "detect"))
        self.assertEqual(
            profile_settings(replaced, "build"),
            {"lead": 2.0, "slide_images": False},
        )
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

    def test_slide_image_setting_requires_a_boolean(self):
        profile = dict(self.profile)
        profile["settings"] = {"build": {"slide_images": 0}}
        self.store.path.write_text(
            json.dumps({"version": 1, "active_profile": "bad", "profiles": {"bad": profile}}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SlidesDocxError, "build.slide_images"):
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


class ServiceJobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = ProfileStore(self.root / "config.json")
        self.store.set_profile("room", make_profile((600, 300, 20, 10), 640, 360))

    def tearDown(self):
        self.temporary.cleanup()

    def test_cancellation_token(self):
        token = CancellationToken()
        self.assertFalse(token.cancelled)
        token.cancel()
        with self.assertRaises(JobCancelledError):
            token.raise_if_cancelled()

    def test_generated_output_suffixes_are_case_insensitive(self):
        self.assertEqual(
            ensure_output_suffix(self.root / "lecture notes", ".docx"),
            self.root / "lecture notes.docx",
        )
        self.assertEqual(
            ensure_output_suffix(self.root / "lecture.DOCX", ".docx"),
            self.root / "lecture.DOCX",
        )
        self.assertEqual(
            ensure_output_suffix(self.root / "contact.JPEG", ".jpg", (".jpeg",)),
            self.root / "contact.JPEG",
        )

    def test_bundled_tool_directory_precedes_path(self):
        tool = self.root / "tools" / "ffmpeg"
        tool.parent.mkdir()
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
        with patch.dict("os.environ", {"SLIDES_DOCX_TOOL_DIR": str(tool.parent)}):
            self.assertEqual(resolve_tool("ffmpeg"), str(tool))

    def test_macos_app_resource_tool_precedes_path(self):
        executable = (
            self.root / "Slides DOCX.app" / "Contents" / "MacOS" / "slides-docx-gui"
        )
        tool = executable.parent.parent / "Resources" / "bin" / "ffmpeg"
        tool.parent.mkdir(parents=True)
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch()
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
        with patch("slides_docx.video.sys.executable", str(executable)), \
             patch("slides_docx.video.shutil.which", return_value=None):
            self.assertEqual(resolve_tool("ffmpeg"), str(tool))

    def test_preview_clamps_time_and_reports_progress(self):
        output = self.root / "preview.png"
        events = []
        with patch("slides_docx.services.validate_video", return_value=self.root / "video.mp4"), \
             patch("slides_docx.services.probe_video", return_value=VideoInfo(10, 640, 360)), \
             patch("slides_docx.services.extract_frame") as extract:
            result = extract_preview(
                PreviewRequest(self.root / "video.mp4", output, 30), events.append
            )
        self.assertAlmostEqual(result.timestamp, 9.85)
        self.assertEqual(events[-1].fraction, 1)
        self.assertEqual(extract.call_args.args[1], result.timestamp)

    def test_detect_uses_services_and_persists_only_requested_settings(self):
        video = self.root / "lecture.mp4"
        output = self.root / "lecture.slide-times"
        contact = self.root / "lecture.contact-sheet"
        events = []
        selection = CropSelection((600, 300, 20, 10), "profile", "room", "fingerprint")
        request = DetectRequest(
            video,
            output=output,
            contact_output=contact,
            threshold=14,
            min_gap=None,
            contact_sheet=True,
            profile="room",
            persist_profile_settings=True,
        )
        with patch("slides_docx.services.validate_video", return_value=video), \
             patch("slides_docx.services.probe_video", return_value=VideoInfo(30, 640, 360)), \
             patch("slides_docx.services.resolve_crop", return_value=selection), \
             patch("slides_docx.services.detect_scene_times", return_value=[10.0]), \
             patch("slides_docx.services.write_timestamp_file"), \
             patch("slides_docx.services.create_contact_sheet"):
            result = detect_slides(request, self.store, events.append)
        self.assertEqual(result.slide_count, 2)
        self.assertEqual(result.slide_times, self.root / "lecture.slide-times.txt")
        self.assertEqual(result.contact_sheet, self.root / "lecture.contact-sheet.jpg")
        saved = profile_settings(self.store.get_profile("room")[1], "detect")
        self.assertEqual(saved, {"threshold": 14, "contact_sheet": True})
        self.assertEqual(events[-1].stage, "done")

    def test_failed_detect_does_not_persist_settings(self):
        request = DetectRequest(
            self.root / "lecture.mp4",
            threshold=14,
            profile="room",
            persist_profile_settings=True,
        )
        selection = CropSelection((600, 300, 20, 10), "profile", "room", "fingerprint")
        with patch("slides_docx.services.validate_video", return_value=request.video), \
             patch("slides_docx.services.probe_video", return_value=VideoInfo(30, 640, 360)), \
             patch("slides_docx.services.resolve_crop", return_value=selection), \
             patch("slides_docx.services.detect_scene_times", side_effect=SlidesDocxError("failed")):
            with self.assertRaisesRegex(SlidesDocxError, "failed"):
                detect_slides(request, self.store)
        self.assertEqual(profile_settings(self.store.get_profile("room")[1], "detect"), {})

    def test_build_reports_result_and_can_persist_to_metadata_profile(self):
        video = self.root / "lecture.mp4"
        vtt = self.root / "lecture.vtt"
        times = self.root / "lecture.slide-times.txt"
        output = self.root / "lecture notes"
        selection = CropSelection((600, 300, 20, 10), "profile", "room", "fingerprint")
        request = BuildRequest(
            video,
            vtt,
            slide_times=times,
            output=output,
            lead=2,
            slide_images=False,
            course_date=date(2026, 9, 17),
            persist_profile_settings=True,
            settings_profile="room",
        )
        with patch("slides_docx.services.validate_video", return_value=video), \
             patch("slides_docx.services.probe_video", return_value=VideoInfo(30, 640, 360)), \
             patch("slides_docx.services.read_timestamp_file", return_value=({}, [10.0])), \
             patch("slides_docx.services.resolve_crop", return_value=selection), \
             patch("slides_docx.services.build_document", return_value=2):
            result = build_docx(request, self.store)
        self.assertEqual(result.slide_count, 2)
        self.assertEqual(result.output, self.root / "2026_09_17-lecture notes.docx")
        self.assertFalse(
            result.settings["slide_images"]
        )
        self.assertEqual(
            profile_settings(self.store.get_profile("room")[1], "build"),
            {"lead": 2, "slide_images": False},
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
        self.assertIn("--contact-output", options)
        build_options = complete(
            self.parser,
            ["slides-docx", "build", "lecture.mp4", "lecture.vtt", "--"],
            4,
        )
        self.assertIn("--slide-images", build_options)
        self.assertIn("--no-slide-images", build_options)
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
