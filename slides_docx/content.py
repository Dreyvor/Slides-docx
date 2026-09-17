import bisect
import html
import math
import re

from .errors import SlidesDocxError


def timestamp_to_seconds(timestamp):
    value = timestamp.strip()
    try:
        if ":" not in value:
            seconds = float(value)
        else:
            parts = value.split(":")
            if len(parts) == 3:
                hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
            elif len(parts) == 2:
                hours, minutes, seconds = 0, int(parts[0]), float(parts[1])
            else:
                raise ValueError
            if minutes < 0 or minutes >= 60 or seconds < 0 or seconds >= 60:
                raise ValueError
            seconds = hours * 3600 + minutes * 60 + seconds
    except ValueError:
        raise ValueError(f"Invalid timestamp: {timestamp}") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"Invalid timestamp: {timestamp}")
    return seconds


def seconds_to_timestamp(seconds):
    seconds = max(0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remainder = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{remainder:06.3f}"


def read_vtt(vtt_file):
    try:
        contents = vtt_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SlidesDocxError(f"Could not read VTT transcript {vtt_file}: {exc}") from exc
    cues = []
    for block in re.split(r"\n\s*\n", contents):
        lines = block.strip().splitlines()
        timestamp_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timestamp_index is None:
            continue
        start = lines[timestamp_index].split("-->", 1)[0].strip()
        try:
            start_seconds = timestamp_to_seconds(start)
        except ValueError:
            continue
        text = " ".join(line.strip() for line in lines[timestamp_index + 1:] if line.strip())
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        if text:
            cues.append((start_seconds, text))
    return cues


def transcript_by_slide(cues, slide_starts):
    transcripts = [[] for _ in slide_starts]
    for cue_time, text in cues:
        index = bisect.bisect_right(slide_starts, cue_time) - 1
        if 0 <= index < len(transcripts):
            transcripts[index].append(text)
    return transcripts


def screenshot_time(slide_start, slide_end, lead):
    duration = slide_end - slide_start
    if duration <= 0:
        return slide_start
    desired = slide_end - lead
    minimum_safe_time = slide_start + min(1.0, duration * 0.25)
    if desired <= minimum_safe_time:
        desired = slide_start + duration * 0.75
    desired = min(desired, slide_end - 0.15)
    return max(slide_start, desired)
