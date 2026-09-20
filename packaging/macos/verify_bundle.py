"""Validate native architecture and linkage inside the macOS application."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from slides_docx import __version__


ALLOWED_ABSOLUTE_PREFIXES = ("/System/Library/", "/usr/lib/")
ALLOWED_RELATIVE_PREFIXES = ("@rpath/", "@loader_path/", "@executable_path/")


def output(*command: str) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()


def macho_files(app: Path) -> list[Path]:
    found = []
    for path in app.rglob("*"):
        if path.is_file() and "Mach-O" in output("file", "-b", str(path)):
            found.append(path)
    return found


def dependencies(binary: Path) -> list[str]:
    lines = output("otool", "-L", str(binary)).splitlines()[1:]
    return [line.strip().split(" (", 1)[0] for line in lines if line.strip()]


def verify(app: Path) -> None:
    resources = app / "Contents" / "Resources"
    required = (
        resources / "bin" / "ffmpeg",
        resources / "bin" / "ffprobe",
        resources / "licenses" / "THIRD_PARTY_NOTICES.md",
        resources / "licenses" / "ffmpeg" / "COPYING.LGPLv2.1",
        resources / "licenses" / "ffmpeg" / "build-configuration.txt",
        resources / "licenses" / "ffmpeg" / "SOURCE.md",
        resources / "licenses" / "python" / "PYTHON_PACKAGES.md",
        resources / "licenses" / "python" / "PySide6-LGPL-3.0.txt",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Bundle is missing: " + ", ".join(missing))
    for tool in required[:2]:
        if not os.access(tool, os.X_OK):
            raise RuntimeError(f"Bundled tool is not executable: {tool}")
    if not any(resources.glob("*.icns")):
        raise RuntimeError("Bundle does not contain an ICNS application icon")

    with (app / "Contents" / "Info.plist").open("rb") as stream:
        metadata = plistlib.load(stream)
    expected_metadata = {
        "CFBundleIdentifier": "io.github.dreyvor.slidesdocx",
        "CFBundleName": "Slides DOCX",
        "CFBundleDisplayName": "Slides DOCX",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.education",
        "NSHighResolutionCapable": True,
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            raise RuntimeError(
                f"Info.plist {key} is {metadata.get(key)!r}; expected {expected!r}"
            )

    binaries = macho_files(app)
    if not binaries:
        raise RuntimeError("The application contains no Mach-O executables")
    failures = []
    for binary in binaries:
        architectures = output("lipo", "-archs", str(binary)).split()
        if architectures != ["arm64"]:
            failures.append(f"{binary}: architectures are {' '.join(architectures)}")
        for dependency in dependencies(binary):
            if dependency.startswith(ALLOWED_ABSOLUTE_PREFIXES + ALLOWED_RELATIVE_PREFIXES):
                continue
            failures.append(f"{binary}: external dependency {dependency}")
    if failures:
        raise RuntimeError("Invalid native bundle:\n" + "\n".join(failures))

    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)],
        check=True,
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_bundle.py APPLICATION.app")
    application = Path(sys.argv[1]).resolve()
    if sys.platform != "darwin":
        raise SystemExit("Bundle verification must run on macOS")
    if not application.is_dir():
        raise SystemExit(f"Application bundle does not exist: {application}")
    try:
        verify(application)
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        raise SystemExit(str(error)) from error
