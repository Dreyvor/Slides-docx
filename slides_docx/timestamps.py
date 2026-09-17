import os
import tempfile
from pathlib import Path

from .errors import SlidesDocxError


def read_timestamp_file(path, video_duration=None):
    path = Path(path)
    if not path.is_file():
        raise SlidesDocxError(f"Slide-times file does not exist: {path}")
    metadata = {}
    times = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SlidesDocxError(f"Could not read slide-times file {path}: {exc}") from exc
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            key, separator, value = line[1:].partition(":")
            if separator:
                metadata[key.strip()] = value.strip()
            continue
        try:
            value = float(line)
        except ValueError:
            continue
        if value > 0 and (video_duration is None or value < video_duration):
            times.append(value)
    return metadata, sorted(set(times))


def write_timestamp_file(path, times, crop_selection):
    path = Path(path)
    if not path.parent.is_dir():
        raise SlidesDocxError(f"Output directory does not exist: {path.parent}")
    lines = ["# slides-docx: 1", f"# crop-mode: {crop_selection.mode}"]
    if crop_selection.profile_name:
        lines.append(f"# crop-profile: {crop_selection.profile_name}")
    if crop_selection.fingerprint:
        lines.append(f"# crop-fingerprint: {crop_selection.fingerprint}")
    lines.extend(f"{value:.6f}".rstrip("0").rstrip(".") for value in times)
    contents = "\n".join(lines) + "\n"
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(contents)
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise SlidesDocxError(f"Could not write slide times to {path}: {exc}") from exc
