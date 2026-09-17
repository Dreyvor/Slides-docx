import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import SlidesDocxError


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int


def require_tool(name):
    if shutil.which(name) is None:
        raise SlidesDocxError(
            f"{name} was not found on PATH. Install FFmpeg and open a new terminal."
        )


def validate_video(video):
    video = Path(video)
    if not video.is_file():
        raise SlidesDocxError(f"Video does not exist or is not a file: {video}")
    return video


def probe_video(video):
    video = validate_video(video)
    require_tool("ffprobe")
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(video),
    ]
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        duration = float(data["format"]["duration"])
        width = int(stream["width"])
        height = int(stream["height"])
    except (subprocess.CalledProcessError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            detail = f" ({exc.stderr.strip()})"
        raise SlidesDocxError(f"Could not read video information for {video}{detail}") from exc
    if duration <= 0 or width <= 0 or height <= 0:
        raise SlidesDocxError(f"Video has invalid duration or dimensions: {video}")
    return VideoInfo(duration, width, height)


def extract_frame(video, timestamp, output, crop=None, image_format="image2"):
    require_tool("ffmpeg")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{timestamp:.3f}", "-i", str(video),
    ]
    if crop:
        command += ["-vf", f"crop={crop}"]
    command += ["-frames:v", "1", "-q:v", "2", "-f", image_format, str(output)]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise SlidesDocxError(
            f"FFmpeg could not extract a frame at {timestamp:.3f}s from {video}"
        ) from exc


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
    video, threshold, crop=None, minimum_gap=MIN_SCENE_CHANGE_GAP
):
    require_tool("ffmpeg")
    filters = []
    if crop:
        filters.append(f"crop={crop}")
    filters.extend([
        "scale=640:-2",
        f"scdet=threshold={threshold:g}",
        "metadata=mode=print:key=lavfi.scd.time:file=-",
    ])
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-i", str(video),
        "-map", "0:v:0", "-vf", ",".join(filters),
        "-an", "-sn", "-dn", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as exc:
        raise SlidesDocxError(f"FFmpeg slide detection failed for {video}") from exc
    times = []
    for line in result.stdout.splitlines():
        match = _SCENE_TIME.fullmatch(line.strip())
        if match:
            times.append(float(match.group(1)))
    return merge_rapid_scene_changes(times, minimum_gap)
