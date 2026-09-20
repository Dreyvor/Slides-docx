import argparse
import math
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import __version__
from .content import seconds_to_timestamp, timestamp_to_seconds
from .errors import SlidesDocxError
from .profiles import (
    ProfileStore,
    format_crop,
    profile_fingerprint,
    profile_settings,
    validate_profile_name,
)
from .services import (
    DEFAULT_CONTACT_SHEET,
    DEFAULT_LEAD,
    DEFAULT_MIN_GAP,
    DEFAULT_THRESHOLD,
    BuildRequest,
    DetectRequest,
    PreviewRequest,
    build_docx,
    describe_crop,
    detect_slides,
    extract_preview,
    persist_explicit_settings as _persist_explicit_settings,
    resolve_command_settings as _resolve_command_settings,
    save_crop_profile,
)
# Kept as module attributes for completion/tests to verify that completion does not
# invoke media processing.
from .video import detect_scene_times, probe_video


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
    detect.add_argument("--contact-output", type=Path, help="Contact-sheet output path")
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
    build.add_argument(
        "--slide-images",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Include or omit slide screenshots in the DOCX (built-in default: included)",
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
    with tempfile.TemporaryDirectory(prefix="slides_docx_preview_") as directory:
        preview = Path(directory) / "preview.png"
        result = extract_preview(PreviewRequest(args.video, preview, args.at))
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
            f"Selecting slide area from {seconds_to_timestamp(result.timestamp)}. "
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
    save_crop_profile(args.profile, crop, result.info, _store())
    print(
        f"Saved and activated crop profile '{args.profile}': "
        f"{format_crop(crop)} for {result.info.width}x{result.info.height} video"
    )
    return 0


def handle_detect(args):
    def progress(event):
        if event.stage == "contact-sheet" and event.current == 0:
            print(event.message)

    result = detect_slides(
        DetectRequest(
            video=args.video,
            output=args.output,
            contact_output=args.contact_output,
            threshold=args.threshold,
            min_gap=args.min_gap,
            contact_sheet=args.contact_sheet,
            crop=args.crop,
            profile=args.profile,
            no_crop=args.no_crop,
            persist_profile_settings=bool(args.profile),
        ),
        store=_store(),
        progress=progress,
    )
    _print_crop(result.crop)
    print(f"Detected {len(result.times)} slide changes.")
    print(f"Wrote: {result.slide_times}")
    if result.contact_sheet:
        print(f"Created: {result.contact_sheet}")
    return 0


def handle_build(args):
    def progress(event):
        if event.stage == "build" and event.message.startswith("Slide "):
            print(event.message)

    result = build_docx(
        BuildRequest(
            video=args.video,
            vtt=args.vtt,
            slide_times=args.slide_times,
            output=args.output,
            lead=args.lead,
            slide_images=args.slide_images,
            course_date=args.date,
            crop=args.crop,
            profile=args.profile,
            no_crop=args.no_crop,
            persist_profile_settings=bool(args.profile),
        ),
        store=_store(),
        progress=progress,
    )
    _print_crop(result.crop)
    print(f"Video duration: {seconds_to_timestamp(result.info.duration)}")
    print(f"Detected {result.slide_count} slides")
    print(f"Created: {result.output}")
    return 0


def _print_crop(selection):
    print(describe_crop(selection))


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
    if "slide_images" in build:
        enabled = "yes" if build["slide_images"] else "no"
        values.append(f"slide-images={enabled}")
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
