"""Render a portable pyside6-deploy configuration for the current checkout."""

from __future__ import annotations

import sys
from pathlib import Path


def main(template_name: str, output_name: str) -> None:
    repository = Path(__file__).resolve().parent.parent
    template = Path(template_name).resolve()
    output = Path(output_name).resolve()
    rendered = template.read_text(encoding="utf-8")
    replacements = {
        "@PROJECT_DIR@": str(repository),
        "@PYTHON_PATH@": str(Path(sys.executable).resolve()),
    }
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    unresolved = [marker for marker in replacements if marker in rendered]
    if unresolved:
        raise SystemExit(f"Unresolved deployment markers: {', '.join(unresolved)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: render_deploy_spec.py TEMPLATE OUTPUT")
    main(sys.argv[1], sys.argv[2])
