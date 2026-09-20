"""Collect license metadata for dependencies included in the desktop bundle."""

import shutil
import sys
from importlib import metadata
from pathlib import Path


PACKAGES = (
    "PySide6",
    "shiboken6",
    "opencv-python",
    "numpy",
    "python-docx",
    "platformdirs",
)


def main(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    inventory = ["# Bundled Python packages", ""]
    for package in PACKAGES:
        distribution = metadata.distribution(package)
        name = distribution.metadata.get("Name", package)
        version = distribution.version
        expression = distribution.metadata.get("License-Expression")
        license_name = expression or distribution.metadata.get("License", "See files below")
        if "\n" in license_name or len(license_name) > 120:
            license_name = "See bundled license files"
        inventory.append(f"- {name} {version}: {license_name}")
        package_output = output / f"{name}-{version}"
        copied = 0
        for item in distribution.files or ():
            filename = Path(str(item)).name.lower()
            if not filename.startswith(("license", "copying", "notice")):
                continue
            source = Path(distribution.locate_file(item))
            if not source.is_file():
                continue
            package_output.mkdir(parents=True, exist_ok=True)
            destination = package_output / Path(str(item)).name
            if destination.exists():
                destination = package_output / f"{copied}-{destination.name}"
            shutil.copy2(source, destination)
            copied += 1
    (output / "PYTHON_PACKAGES.md").write_text(
        "\n".join(inventory) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main(sys.argv[1])
