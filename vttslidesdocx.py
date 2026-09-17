#!/usr/bin/env python3
"""Compatibility wrapper for the original vttslidesdocx.py command."""

from slides_docx.cli import legacy_build_main


if __name__ == "__main__":
    raise SystemExit(legacy_build_main())
