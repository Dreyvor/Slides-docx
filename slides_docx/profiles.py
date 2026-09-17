import hashlib
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from platformdirs import user_config_dir

from .errors import SlidesDocxError


PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PROFILE_SETTING_KEYS = {
    "detect": {"threshold", "min_gap", "contact_sheet"},
    "build": {"lead"},
}


def validate_profile_name(name):
    if not PROFILE_NAME.fullmatch(name):
        raise SlidesDocxError(
            "Profile names may contain letters, numbers, dots, underscores, and hyphens."
        )
    return name


def parse_crop(value):
    try:
        width, height, x, y = (int(part) for part in value.split(":"))
    except (ValueError, AttributeError):
        raise SlidesDocxError("Crop must use W:H:X:Y with integer values.") from None
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        raise SlidesDocxError("Crop width and height must be positive; X and Y cannot be negative.")
    return width, height, x, y


def format_crop(crop):
    return ":".join(str(value) for value in crop)


def validate_crop_bounds(crop, video_width, video_height):
    width, height, x, y = crop
    if x + width > video_width or y + height > video_height:
        raise SlidesDocxError(
            f"Crop {format_crop(crop)} exceeds video dimensions "
            f"{video_width}x{video_height}."
        )
    return crop


def make_profile(crop, video_width, video_height):
    width, height, x, y = validate_crop_bounds(crop, video_width, video_height)
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "source_width": video_width,
        "source_height": video_height,
        "normalized": {
            "x": x / video_width,
            "y": y / video_height,
            "width": width / video_width,
            "height": height / video_height,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def profile_fingerprint(profile):
    stable = {
        key: profile[key]
        for key in ("x", "y", "width", "height", "source_width", "source_height")
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def profile_settings(profile, command):
    """Return a copy of the saved settings for a command."""
    return dict(profile.get("settings", {}).get(command, {}))


def _validate_settings(settings, profile_name, path):
    if settings is None:
        return
    if not isinstance(settings, dict):
        raise SlidesDocxError(
            f"Crop profile '{profile_name}' has invalid settings in configuration {path}"
        )
    for command, values in settings.items():
        if command not in PROFILE_SETTING_KEYS or not isinstance(values, dict):
            raise SlidesDocxError(
                f"Crop profile '{profile_name}' has invalid settings group "
                f"'{command}' in configuration {path}"
            )
        unknown = set(values) - PROFILE_SETTING_KEYS[command]
        if unknown:
            setting = sorted(unknown)[0]
            raise SlidesDocxError(
                f"Crop profile '{profile_name}' has unknown setting "
                f"'{command}.{setting}' in configuration {path}"
            )
        for setting, value in values.items():
            key = f"{command}.{setting}"
            if setting == "contact_sheet":
                valid = isinstance(value, bool)
            else:
                valid = (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0
                )
                if setting == "threshold":
                    valid = valid and value <= 100
            if not valid:
                raise SlidesDocxError(
                    f"Crop profile '{profile_name}' has invalid setting '{key}' "
                    f"in configuration {path}"
                )


def scale_profile(profile, video_width, video_height):
    source_width = int(profile["source_width"])
    source_height = int(profile["source_height"])
    if (source_width, source_height) == (video_width, video_height):
        crop = (
            int(profile["width"]), int(profile["height"]),
            int(profile["x"]), int(profile["y"]),
        )
        return validate_crop_bounds(crop, video_width, video_height)

    source_ratio = source_width / source_height
    target_ratio = video_width / video_height
    if abs(target_ratio - source_ratio) / source_ratio > 0.01:
        raise SlidesDocxError(
            f"Crop profile was selected for {source_width}x{source_height}, but this video is "
            f"{video_width}x{video_height} with a different aspect ratio. Select a new profile."
        )

    normalized = profile.get("normalized") or {
        "x": profile["x"] / source_width,
        "y": profile["y"] / source_height,
        "width": profile["width"] / source_width,
        "height": profile["height"] / source_height,
    }
    x = max(0, min(video_width - 1, round(normalized["x"] * video_width)))
    y = max(0, min(video_height - 1, round(normalized["y"] * video_height)))
    width = max(1, min(video_width - x, round(normalized["width"] * video_width)))
    height = max(1, min(video_height - y, round(normalized["height"] * video_height)))
    return width, height, x, y


class ProfileStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(
            user_config_dir("slides-docx", roaming=True)
        ) / "config.json"

    def load(self):
        if not self.path.exists():
            return {"version": 1, "active_profile": None, "profiles": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SlidesDocxError(f"Could not read crop configuration {self.path}: {exc}") from exc
        if data.get("version") != 1 or not isinstance(data.get("profiles"), dict):
            raise SlidesDocxError(f"Unsupported crop configuration format: {self.path}")
        required = {"x", "y", "width", "height", "source_width", "source_height"}
        for name, profile in data["profiles"].items():
            if not isinstance(profile, dict) or not required.issubset(profile):
                raise SlidesDocxError(
                    f"Crop profile '{name}' is invalid in configuration {self.path}"
                )
            _validate_settings(profile.get("settings"), name, self.path)
        return data

    def save(self, data):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2, sort_keys=True)
                    handle.write("\n")
                os.replace(temporary, self.path)
            except Exception:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
        except OSError as exc:
            raise SlidesDocxError(f"Could not write crop configuration {self.path}: {exc}") from exc

    def set_profile(self, name, profile):
        validate_profile_name(name)
        data = self.load()
        profile = deepcopy(profile)
        existing = data["profiles"].get(name)
        if existing and "settings" in existing and "settings" not in profile:
            profile["settings"] = deepcopy(existing["settings"])
        data["profiles"][name] = profile
        data["active_profile"] = name
        self.save(data)

    def update_settings(self, name, command, settings):
        """Merge reusable command settings into an existing profile."""
        validate_profile_name(name)
        if command not in PROFILE_SETTING_KEYS:
            raise SlidesDocxError(f"Unsupported profile settings group: {command}")
        _validate_settings({command: settings}, name, self.path)
        data = self.load()
        profile = data["profiles"].get(name)
        if profile is None:
            raise SlidesDocxError(f"Crop profile does not exist: {name}")
        saved = profile.setdefault("settings", {}).setdefault(command, {})
        saved.update(settings)
        profile["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save(data)

    def get_profile(self, name=None):
        data = self.load()
        resolved_name = name or data.get("active_profile")
        if not resolved_name:
            return None, None
        profile = data["profiles"].get(resolved_name)
        if profile is None:
            raise SlidesDocxError(f"Crop profile does not exist: {resolved_name}")
        return resolved_name, profile

    def activate(self, name):
        validate_profile_name(name)
        data = self.load()
        if name not in data["profiles"]:
            raise SlidesDocxError(f"Crop profile does not exist: {name}")
        data["active_profile"] = name
        self.save(data)

    def delete(self, name):
        validate_profile_name(name)
        data = self.load()
        if name not in data["profiles"]:
            raise SlidesDocxError(f"Crop profile does not exist: {name}")
        if data.get("active_profile") == name:
            raise SlidesDocxError(
                f"Cannot delete active profile '{name}'. Activate another profile first."
            )
        del data["profiles"][name]
        self.save(data)
