from dataclasses import dataclass

from .errors import SlidesDocxError
from .profiles import (
    format_crop,
    parse_crop,
    profile_fingerprint,
    scale_profile,
    validate_crop_bounds,
)


@dataclass(frozen=True)
class CropSelection:
    crop: tuple | None
    mode: str
    profile_name: str | None = None
    fingerprint: str | None = None

    @property
    def ffmpeg_value(self):
        return format_crop(self.crop) if self.crop else None


def resolve_crop(
    store,
    video_width,
    video_height,
    explicit_crop=None,
    profile_name=None,
    no_crop=False,
    timestamp_metadata=None,
):
    if no_crop:
        return CropSelection(None, "none")
    if explicit_crop:
        crop = validate_crop_bounds(parse_crop(explicit_crop), video_width, video_height)
        return CropSelection(crop, "explicit")

    metadata = timestamp_metadata or {}
    requested_profile = profile_name
    expected_fingerprint = None
    if not requested_profile and metadata:
        mode = metadata.get("crop-mode")
        if mode == "none":
            return CropSelection(None, "none")
        if mode == "explicit":
            raise SlidesDocxError(
                "Slide detection used an explicit crop. Pass the same --crop value when building."
            )
        requested_profile = metadata.get("crop-profile")
        expected_fingerprint = metadata.get("crop-fingerprint")

    resolved_name, profile = store.get_profile(requested_profile)
    if profile is None:
        return CropSelection(None, "none")
    fingerprint = profile_fingerprint(profile)
    if expected_fingerprint and fingerprint != expected_fingerprint:
        raise SlidesDocxError(
            f"Crop profile '{resolved_name}' changed after slide detection. "
            "Rerun detection, or explicitly pass --profile, --crop, or --no-crop."
        )
    crop = scale_profile(profile, video_width, video_height)
    return CropSelection(crop, "profile", resolved_name, fingerprint)
