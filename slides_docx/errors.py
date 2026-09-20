class SlidesDocxError(Exception):
    """An expected error that should be shown without a traceback."""


class JobCancelledError(SlidesDocxError):
    """Raised when a running processing job is cancelled by the user."""
