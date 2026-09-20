"""Desktop application entry point, imported lazily for CLI-only installs."""


def main(argv=None):
    try:
        from .app import main as run
    except ImportError as exc:
        if exc.name and exc.name.startswith("PySide6"):
            raise SystemExit(
                "The desktop interface requires PySide6. Install with "
                "pipx install '.[gui]' from the repository, or download the AppImage."
            ) from exc
        raise
    return run(argv)


__all__ = ["main"]
