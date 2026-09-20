import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import JobCancelledError, SlidesDocxError


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int


def _tool_filename(name):
    return f"{name}.exe" if os.name == "nt" else name


def resolve_tool(name):
    """Find a packaged media tool before falling back to PATH."""
    filename = _tool_filename(name)
    candidates = []
    configured = os.environ.get("SLIDES_DOCX_TOOL_DIR")
    if configured:
        candidates.append(Path(configured) / filename)
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(
            Path(appdir) / "usr" / "lib" / "slides-docx" / "bin" / filename
        )
    executable_dir = Path(sys.executable).resolve().parent
    candidates.extend(
        (
            executable_dir / "tools" / filename,
            executable_dir / "bin" / filename,
            executable_dir / filename,
        )
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def require_tool(name):
    tool = resolve_tool(name)
    if tool is None:
        raise SlidesDocxError(
            f"{name} was not found. Install FFmpeg and open a new terminal, "
            "or use the desktop download that includes it."
        )
    return tool


def _check_cancelled(cancel):
    if cancel is not None and cancel.cancelled:
        raise JobCancelledError("Processing was cancelled.")


def _run_process(command, error_message, cancel=None):
    _check_cancelled(cancel)
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    while process.poll() is None:
        if cancel is not None and cancel.cancelled:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            raise JobCancelledError("Processing was cancelled.")
        time.sleep(0.05)
    _stdout, stderr = process.communicate()
    if process.returncode:
        detail = f" ({stderr.strip()})" if stderr and stderr.strip() else ""
        raise SlidesDocxError(f"{error_message}{detail}")


def validate_video(video):
    video = Path(video)
    if not video.is_file():
        raise SlidesDocxError(f"Video does not exist or is not a file: {video}")
    return video


def probe_video(video):
    video = validate_video(video)
    command = [
        require_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(video),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        duration = float(data["format"]["duration"])
        width = int(stream["width"])
        height = int(stream["height"])
    except (
        subprocess.CalledProcessError,
        KeyError,
        IndexError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            detail = f" ({exc.stderr.strip()})"
        raise SlidesDocxError(
            f"Could not read video information for {video}{detail}"
        ) from exc
    if duration <= 0 or width <= 0 or height <= 0:
        raise SlidesDocxError(f"Video has invalid duration or dimensions: {video}")
    return VideoInfo(duration, width, height)


def extract_frame(
    video, timestamp, output, crop=None, image_format="image2", cancel=None
):
    command = [
        require_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{timestamp:.3f}", "-i", str(video),
    ]
    if crop:
        command += ["-vf", f"crop={crop}"]
    command += ["-frames:v", "1"]
    if Path(output).suffix.lower() in {".jpg", ".jpeg"}:
        command += ["-q:v", "2"]
    command += ["-f", image_format, str(output)]
    _run_process(
        command,
        f"FFmpeg could not extract a frame at {timestamp:.3f}s from {video}",
        cancel,
    )


_SCENE_TIME = re.compile(r"^lavfi\.scd\.time=([0-9]+(?:\.[0-9]+)?)$")
MIN_SCENE_CHANGE_GAP = 0.8


def merge_rapid_scene_changes(times, minimum_gap=MIN_SCENE_CHANGE_GAP):
    """Keep the first timestamp from each cluster of rapid detections."""
    ordered = sorted(set(times))
    if not ordered:
        return []
    merged = [ordered[0]]
    previous = ordered[0]
    for timestamp in ordered[1:]:
        if timestamp - previous >= minimum_gap:
            merged.append(timestamp)
        previous = timestamp
    return merged


def detect_scene_times(
    video,
    threshold,
    crop=None,
    minimum_gap=MIN_SCENE_CHANGE_GAP,
    duration=None,
    progress=None,
    cancel=None,
):
    filters = []
    if crop:
        filters.append(f"crop={crop}")
    filters.extend(
        [
            "scale=640:-2",
            f"scdet=threshold={threshold:g}",
            "metadata=mode=print:key=lavfi.scd.time:file=-",
        ]
    )
    command = [
        require_tool("ffmpeg"), "-hide_banner", "-nostdin", "-nostats",
        "-progress", "pipe:2", "-i", str(video),
        "-map", "0:v:0", "-vf", ",".join(filters),
        "-an", "-sn", "-dn", "-f", "null", "-",
    ]
    _check_cancelled(cancel)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    metadata_lines = []

    def collect_metadata():
        metadata_lines.extend(process.stdout)

    reader = threading.Thread(target=collect_metadata, daemon=True)
    reader.start()
    errors = []
    try:
        for line in process.stderr:
            stripped = line.strip()
            if stripped.startswith("out_time_us="):
                try:
                    current = int(stripped.partition("=")[2]) / 1_000_000
                except ValueError:
                    continue
                if progress:
                    progress(current, duration)
            elif stripped and "=" not in stripped:
                errors.append(stripped)
            if cancel is not None and cancel.cancelled:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise JobCancelledError("Processing was cancelled.")
        return_code = process.wait()
    finally:
        reader.join(timeout=2)
    if return_code:
        detail = f" ({' '.join(errors[-3:])})" if errors else ""
        raise SlidesDocxError(f"FFmpeg slide detection failed for {video}{detail}")
    times = []
    for line in metadata_lines:
        match = _SCENE_TIME.fullmatch(line.strip())
        if match:
            times.append(float(match.group(1)))
    return merge_rapid_scene_changes(times, minimum_gap)
