"""Reusable application jobs shared by the CLI and desktop interface."""

import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from .contact_sheet import create_contact_sheet
from .crop import CropSelection, resolve_crop
from .document import build_document
from .errors import JobCancelledError, SlidesDocxError
from .profiles import ProfileStore, make_profile, profile_settings, validate_profile_name
from .timestamps import read_timestamp_file, write_timestamp_file
from .video import (
    MIN_SCENE_CHANGE_GAP,
    VideoInfo,
    detect_scene_times,
    extract_frame,
    probe_video,
    validate_video,
)


DEFAULT_THRESHOLD = 12.0
DEFAULT_MIN_GAP = MIN_SCENE_CHANGE_GAP
DEFAULT_CONTACT_SHEET = True
DEFAULT_LEAD = 5.0


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str = ""
    current: float | None = None
    total: float | None = None

    @property
    def fraction(self):
        if self.current is None or not self.total:
            return None
        return max(0.0, min(1.0, self.current / self.total))


class CancellationToken:
    def __init__(self):
        self._event = threading.Event()

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        self._event.set()

    def raise_if_cancelled(self):
        if self.cancelled:
            raise JobCancelledError("Processing was cancelled.")


ProgressCallback = Callable[[ProgressEvent], None]


def _emit(callback, stage, message="", current=None, total=None):
    if callback:
        callback(ProgressEvent(stage, message, current, total))


@dataclass(frozen=True)
class PreviewRequest:
    video: Path
    output: Path
    timestamp: float = 30.0


@dataclass(frozen=True)
class PreviewResult:
    video: Path
    image: Path
    timestamp: float
    info: VideoInfo


@dataclass(frozen=True)
class DetectRequest:
    video: Path
    output: Path | None = None
    contact_output: Path | None = None
    threshold: float | None = None
    min_gap: float | None = None
    contact_sheet: bool | None = None
    crop: str | None = None
    profile: str | None = None
    no_crop: bool = False
    persist_profile_settings: bool = False
    settings_profile: str | None = None


@dataclass(frozen=True)
class DetectResult:
    video: Path
    info: VideoInfo
    slide_times: Path
    contact_sheet: Path | None
    times: tuple[float, ...]
    crop: CropSelection
    settings: dict

    @property
    def slide_count(self):
        return len(self.times) + 1


@dataclass(frozen=True)
class BuildRequest:
    video: Path
    vtt: Path
    slide_times: Path | None = None
    output: Path | None = None
    lead: float | None = None
    course_date: date | None = None
    crop: str | None = None
    profile: str | None = None
    no_crop: bool = False
    persist_profile_settings: bool = False
    settings_profile: str | None = None


@dataclass(frozen=True)
class BuildResult:
    video: Path
    info: VideoInfo
    output: Path
    slide_times: Path
    slide_count: int
    crop: CropSelection
    settings: dict


def resolve_command_settings(store, profile_name, command, explicit, defaults):
    saved = {}
    if profile_name:
        _resolved_name, profile = store.get_profile(profile_name)
        saved = profile_settings(profile, command)
    return {
        key: value if value is not None else saved.get(key, defaults[key])
        for key, value in explicit.items()
    }


def persist_explicit_settings(store, profile_name, command, explicit):
    if not profile_name:
        return
    updates = {key: value for key, value in explicit.items() if value is not None}
    if updates:
        store.update_settings(profile_name, command, updates)


def extract_preview(request, progress=None, cancel=None):
    cancel = cancel or CancellationToken()
    cancel.raise_if_cancelled()
    video = validate_video(request.video)
    info = probe_video(video)
    timestamp = min(request.timestamp, max(0.0, info.duration - 0.15))
    output = Path(request.output)
    if not output.parent.is_dir():
        raise SlidesDocxError(f"Preview directory does not exist: {output.parent}")
    _emit(progress, "preview", "Extracting preview frame…", 0, 1)
    extract_frame(video, timestamp, output, image_format="image2", cancel=cancel)
    _emit(progress, "preview", "Preview ready", 1, 1)
    return PreviewResult(video, output, timestamp, info)


def save_crop_profile(name, crop, info, store=None):
    validate_profile_name(name)
    store = store or ProfileStore()
    profile = make_profile(crop, info.width, info.height)
    store.set_profile(name, profile)
    return profile


def detect_slides(request, store=None, progress=None, cancel=None):
    store = store or ProfileStore()
    cancel = cancel or CancellationToken()
    cancel.raise_if_cancelled()
    video = validate_video(request.video)
    info = probe_video(video)
    selection = resolve_crop(
        store,
        info.width,
        info.height,
        explicit_crop=request.crop,
        profile_name=request.profile,
        no_crop=request.no_crop,
    )
    explicit = {
        "threshold": request.threshold,
        "min_gap": request.min_gap,
        "contact_sheet": request.contact_sheet,
    }
    settings = resolve_command_settings(
        store,
        selection.profile_name,
        "detect",
        explicit,
        {
            "threshold": DEFAULT_THRESHOLD,
            "min_gap": DEFAULT_MIN_GAP,
            "contact_sheet": DEFAULT_CONTACT_SHEET,
        },
    )
    output = request.output or video.with_name(f"{video.stem}.slide-times.txt")
    output = Path(output)
    if output.resolve() == video.resolve():
        raise SlidesDocxError("Timestamp output must differ from the video path.")

    _emit(progress, "detect", "Detecting slide changes…", 0, info.duration)

    def detection_progress(current, total):
        cancel.raise_if_cancelled()
        _emit(progress, "detect", "", current, total)

    times = detect_scene_times(
        video,
        settings["threshold"],
        selection.ffmpeg_value,
        settings["min_gap"],
        duration=info.duration,
        progress=detection_progress,
        cancel=cancel,
    )
    cancel.raise_if_cancelled()
    write_timestamp_file(output, times, selection)
    contact_output = None
    if settings["contact_sheet"]:
        contact_output = Path(
            request.contact_output
            or video.with_name(f"{video.stem}.contact-sheet.jpg")
        )
        _emit(
            progress,
            "contact-sheet",
            "Creating contact sheet…",
            0,
            len(times) + 1,
        )

        def contact_progress(current, total):
            cancel.raise_if_cancelled()
            _emit(progress, "contact-sheet", "", current, total)

        create_contact_sheet(
            video,
            times,
            info.duration,
            contact_output,
            crop=selection.ffmpeg_value,
            progress=contact_progress,
            cancel=cancel,
        )
    settings_profile = request.settings_profile or request.profile
    if request.persist_profile_settings and settings_profile:
        persist_explicit_settings(store, settings_profile, "detect", explicit)
    _emit(progress, "done", f"Detected {len(times) + 1} slides", 1, 1)
    return DetectResult(
        video,
        info,
        output,
        contact_output,
        tuple(times),
        selection,
        settings,
    )


def build_docx(request, store=None, progress=None, cancel=None):
    store = store or ProfileStore()
    cancel = cancel or CancellationToken()
    cancel.raise_if_cancelled()
    video = validate_video(request.video)
    info = probe_video(video)
    slide_file = request.slide_times or video.with_name(
        f"{video.stem}.slide-times.txt"
    )
    slide_file = Path(slide_file)
    metadata, times = read_timestamp_file(slide_file, info.duration)
    selection = resolve_crop(
        store,
        info.width,
        info.height,
        explicit_crop=request.crop,
        profile_name=request.profile,
        no_crop=request.no_crop,
        timestamp_metadata=metadata,
    )
    explicit = {"lead": request.lead}
    settings = resolve_command_settings(
        store,
        selection.profile_name,
        "build",
        explicit,
        {"lead": DEFAULT_LEAD},
    )
    output = Path(request.output or video.with_suffix(".docx"))
    if request.course_date:
        output = output.with_name(
            request.course_date.strftime("%Y_%m_%d-") + output.name
        )
    protected_paths = {video.resolve(), Path(request.vtt).resolve(), slide_file.resolve()}
    if output.resolve() in protected_paths:
        raise SlidesDocxError("DOCX output must differ from all input files.")
    _emit(progress, "build", "Building Word document…", 0, len(times) + 1)

    def build_progress(current, total, message):
        cancel.raise_if_cancelled()
        _emit(progress, "build", message, current, total)

    slide_count = build_document(
        video,
        Path(request.vtt),
        times,
        info.duration,
        output,
        lead=settings["lead"],
        crop=selection.ffmpeg_value,
        progress=build_progress,
        cancel=cancel,
    )
    settings_profile = request.settings_profile or request.profile
    if request.persist_profile_settings and settings_profile:
        persist_explicit_settings(store, settings_profile, "build", explicit)
    _emit(progress, "done", f"Created {output.name}", 1, 1)
    return BuildResult(
        video, info, output, slide_file, slide_count, selection, settings
    )


def describe_crop(selection):
    if selection.crop is None:
        return "Slide area: full video frame"
    if selection.profile_name:
        return (
            f"Slide area: profile '{selection.profile_name}' "
            f"({selection.ffmpeg_value})"
        )
    return f"Slide area: explicit crop ({selection.ffmpeg_value})"
