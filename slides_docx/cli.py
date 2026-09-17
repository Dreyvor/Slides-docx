import argparse
import math
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .content import seconds_to_timestamp, timestamp_to_seconds
from .contact_sheet import create_contact_sheet
from .crop import resolve_crop
from .document import build_document
from .errors import SlidesDocxError
from .profiles import (
    ProfileStore,
    format_crop,
    make_profile,
    profile_fingerprint,
    profile_settings,
    validate_profile_name,
)
from .timestamps import read_timestamp_file, write_timestamp_file
from .video import (
    MIN_SCENE_CHANGE_GAP,
    detect_scene_times,
    extract_frame,
    probe_video,
    validate_video,
)


DEFAULT_THRESHOLD = 12.0
DEFAULT_MIN_GAP = MIN_SCENE_CHANGE_GAP
DEFAULT_CONTACT_SHEET = True
DEFAULT_LEAD = 5.0


def course_date(value):
    if len(value) != 10:
        raise argparse.ArgumentTypeError("Date must use DD.MM.YYYY format")
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Date must be a valid calendar date in DD.MM.YYYY format"
        ) from None


def time_value(value):
    try:
        return timestamp_to_seconds(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def threshold_value(value):
    try:
        threshold = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Threshold must be a number from 0 to 100") from None
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise argparse.ArgumentTypeError("Threshold must be from 0 to 100")
    return threshold


def lead_value(value):
    try:
        lead = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Lead time must be a non-negative number") from None
    if not math.isfinite(lead) or lead < 0:
        raise argparse.ArgumentTypeError("Lead time must be a non-negative number")
    return lead


def minimum_gap_value(value):
    try:
        gap = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Minimum scene-change gap must be a non-negative number"
        ) from None
    if not math.isfinite(gap) or gap < 0:
        raise argparse.ArgumentTypeError(
            "Minimum scene-change gap must be a non-negative number"
        )
    return gap


def add_crop_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--crop", metavar="W:H:X:Y", help="Use an explicit crop")
    group.add_argument("--profile", help="Use a named saved crop profile")
    group.add_argument("--no-crop", action="store_true", help="Use the full video frame")


def create_parser():
    parser = argparse.ArgumentParser(
        prog="slides-docx",
        description="Detect presentation slides and create a slide-aligned DOCX transcript.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    select = commands.add_parser("select", help="Visually select and save the slide area")
    select.add_argument("video", type=Path)
    select.add_argument("--profile", default="default", help="Profile name (default: default)")
    select.add_argument(
        "--at", type=time_value, default=30.0, metavar="TIMESTAMP",
        help="Preview time as seconds, MM:SS, or HH:MM:SS (default: 30)",
    )
    select.set_defaults(handler=handle_select)

    detect = commands.add_parser("detect", help="Detect slide-change timestamps")
    detect.add_argument("video", type=Path)
    detect.add_argument("--output", type=Path, help="Timestamp output path")
    detect.add_argument(
        "--threshold",
        type=threshold_value,
        default=None,
        help="Scene-change threshold (built-in default: 12)",
    )
    detect.add_argument(
        "--min-gap",
        type=minimum_gap_value,
        default=None,
        metavar="SECONDS",
        help=(
            "Merge consecutive detections closer than this many seconds; "
            "use 0 to disable (built-in default: 0.8)"
        ),
    )
    detect.add_argument(
        "--contact-sheet",
        action=argparse.BooleanOptionalAction,
        dest="contact_sheet",
        default=None,
        help="Create or skip the slide contact sheet (built-in default: enabled)",
    )
    add_crop_arguments(detect)
    detect.set_defaults(handler=handle_detect)

    build = commands.add_parser("build", help="Build the DOCX from a video and VTT")
    build.add_argument("video", type=Path)
    build.add_argument("vtt", type=Path)
    build.add_argument("--slide-times", type=Path, help="Slide-times input path")
    build.add_argument("--output", type=Path, help="DOCX output path")
    build.add_argument(
        "--lead",
        type=lead_value,
        default=None,
        help="Screenshot lead time in seconds (built-in default: 5)",
    )
    build.add_argument("--date", type=course_date, metavar="DD.MM.YYYY")
    add_crop_arguments(build)
    build.set_defaults(handler=handle_build)

    profiles = commands.add_parser("profiles", help="List or manage crop profiles")
    profiles.add_argument("action", nargs="?", choices=("list", "delete", "activate"), default="list")
    profiles.add_argument("name", nargs="?")
    profiles.set_defaults(handler=handle_profiles)

    completion = commands.add_parser(
        "completion", help="Print a shell completion activation script"
    )
    completion.add_argument("shell", choices=("bash", "zsh", "fish", "powershell"))
    completion.set_defaults(handler=handle_completion)
    return parser


def _store():
    return ProfileStore()


def handle_select(args):
    validate_profile_name(args.profile)
    video = validate_video(args.video)
    info = probe_video(video)
    preview_time = min(args.at, max(0.0, info.duration - 0.15))
    with tempfile.TemporaryDirectory(prefix="slides_docx_preview_") as directory:
        preview = Path(directory) / "preview.png"
        extract_frame(video, preview_time, preview, image_format="image2")
        try:
            import cv2
        except ImportError as exc:
            raise SlidesDocxError(
                "OpenCV is required for visual selection. Reinstall the package, or use --crop."
            ) from exc
        image = cv2.imread(str(preview))
        if image is None:
            raise SlidesDocxError("OpenCV could not open the extracted preview frame.")
        print(
            f"Selecting slide area from {seconds_to_timestamp(preview_time)}. "
            "Drag a rectangle and press Enter or Space; press C to cancel."
        )
        try:
            x, y, width, height = cv2.selectROI(
                "Select slides area", image, showCrosshair=True, fromCenter=False
            )
        except Exception as exc:
            raise SlidesDocxError(
                "Could not open the OpenCV selection window. Use a desktop session or pass --crop."
            ) from exc
        finally:
            cv2.destroyAllWindows()
    crop = (int(width), int(height), int(x), int(y))
    if crop[0] <= 0 or crop[1] <= 0:
        print("Selection cancelled; crop configuration was not changed.")
        return 0
    profile = make_profile(crop, info.width, info.height)
    _store().set_profile(args.profile, profile)
    print(
        f"Saved and activated crop profile '{args.profile}': "
        f"{format_crop(crop)} for {info.width}x{info.height} video"
    )
    return 0


def handle_detect(args):
    video = validate_video(args.video)
    info = probe_video(video)
    store = _store()
    selection = resolve_crop(
        store, info.width, info.height,
        explicit_crop=args.crop, profile_name=args.profile, no_crop=args.no_crop,
    )
    explicit_settings = {
        "threshold": args.threshold,
        "min_gap": args.min_gap,
        "contact_sheet": args.contact_sheet,
    }
    settings = _resolve_command_settings(
        store,
        selection.profile_name,
        "detect",
        explicit_settings,
        {
            "threshold": DEFAULT_THRESHOLD,
            "min_gap": DEFAULT_MIN_GAP,
            "contact_sheet": DEFAULT_CONTACT_SHEET,
        },
    )
    output = args.output or video.with_name(f"{video.stem}.slide-times.txt")
    if output.resolve() == video.resolve():
        raise SlidesDocxError("Timestamp output must differ from the video path.")
    _print_crop(selection)
    times = detect_scene_times(
        video, settings["threshold"], selection.ffmpeg_value, settings["min_gap"]
    )
    write_timestamp_file(output, times, selection)
    print(f"Detected {len(times)} slide changes.")
    print(f"Wrote: {output}")
    if settings["contact_sheet"]:
        contact_output = video.with_name(f"{video.stem}.contact-sheet.jpg")
        print("Creating contact sheet...")
        create_contact_sheet(
            video,
            times,
            info.duration,
            contact_output,
            crop=selection.ffmpeg_value,
        )
        print(f"Created: {contact_output}")
    _persist_explicit_settings(store, args.profile, "detect", explicit_settings)
    return 0


def handle_build(args):
    video = validate_video(args.video)
    info = probe_video(video)
    store = _store()
    slide_file = args.slide_times or video.with_name(f"{video.stem}.slide-times.txt")
    metadata, times = read_timestamp_file(slide_file, info.duration)
    selection = resolve_crop(
        store, info.width, info.height,
        explicit_crop=args.crop, profile_name=args.profile, no_crop=args.no_crop,
        timestamp_metadata=metadata,
    )
    explicit_settings = {"lead": args.lead}
    settings = _resolve_command_settings(
        store,
        selection.profile_name,
        "build",
        explicit_settings,
        {"lead": DEFAULT_LEAD},
    )
    output = args.output or video.with_suffix(".docx")
    if args.date:
        output = output.with_name(args.date.strftime("%Y_%m_%d-") + output.name)
    protected_paths = {video.resolve(), args.vtt.resolve(), slide_file.resolve()}
    if output.resolve() in protected_paths:
        raise SlidesDocxError("DOCX output must differ from all input files.")
    _print_crop(selection)
    print(f"Video duration: {seconds_to_timestamp(info.duration)}")
    print(f"Detected {len(times) + 1} slides")
    build_document(
        video, args.vtt, times, info.duration, output,
        lead=settings["lead"], crop=selection.ffmpeg_value,
    )
    print(f"Created: {output}")
    _persist_explicit_settings(store, args.profile, "build", explicit_settings)
    return 0


def _resolve_command_settings(store, profile_name, command, explicit, defaults):
    saved = {}
    if profile_name:
        _resolved_name, profile = store.get_profile(profile_name)
        saved = profile_settings(profile, command)
    return {
        key: value if value is not None else saved.get(key, defaults[key])
        for key, value in explicit.items()
    }


def _persist_explicit_settings(store, profile_name, command, explicit):
    if not profile_name:
        return
    updates = {key: value for key, value in explicit.items() if value is not None}
    if updates:
        store.update_settings(profile_name, command, updates)


def _print_crop(selection):
    if selection.crop is None:
        print("Slide area: full video frame")
    elif selection.profile_name:
        print(f"Slide area: profile '{selection.profile_name}' ({selection.ffmpeg_value})")
    else:
        print(f"Slide area: explicit crop ({selection.ffmpeg_value})")


def handle_profiles(args):
    store = _store()
    if args.action == "list":
        if args.name:
            raise SlidesDocxError("The list action does not take a profile name.")
        data = store.load()
        if not data["profiles"]:
            print(f"No crop profiles saved. Configuration: {store.path}")
            return 0
        for name in sorted(data["profiles"]):
            profile = data["profiles"][name]
            marker = "*" if name == data.get("active_profile") else " "
            crop = (profile["width"], profile["height"], profile["x"], profile["y"])
            settings = _format_profile_settings(profile)
            print(
                f"{marker} {name}: {format_crop(crop)} "
                f"at {profile['source_width']}x{profile['source_height']} "
                f"[{profile_fingerprint(profile)}]{settings}"
            )
        print(f"Configuration: {store.path}")
        return 0
    if not args.name:
        raise SlidesDocxError(f"The {args.action} action requires a profile name.")
    if args.action == "delete":
        store.delete(args.name)
        print(f"Deleted crop profile: {args.name}")
    else:
        store.activate(args.name)
        print(f"Activated crop profile: {args.name}")
    return 0


def _format_profile_settings(profile):
    detect = profile_settings(profile, "detect")
    build = profile_settings(profile, "build")
    values = []
    if "threshold" in detect:
        values.append(f"threshold={detect['threshold']:g}")
    if "min_gap" in detect:
        values.append(f"min-gap={detect['min_gap']:g}")
    if "contact_sheet" in detect:
        enabled = "yes" if detect["contact_sheet"] else "no"
        values.append(f"contact-sheet={enabled}")
    if "lead" in build:
        values.append(f"lead={build['lead']:g}")
    return f"; settings: {', '.join(values)}" if values else ""


def handle_completion(args):
    from .completion import render_completion

    print(render_completion(args.shell), end="")
    return 0


def _handle_completion_query(argv):
    from .completion import complete

    if len(argv) < 2:
        return 0
    try:
        cursor = int(argv[0])
    except ValueError:
        return 0
    for candidate in complete(create_parser(), argv[1:], cursor):
        print(candidate)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "_complete":
        return _handle_completion_query(argv[1:])
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except SlidesDocxError as exc:
        parser.error(str(exc))
