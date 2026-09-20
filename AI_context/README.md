# AI onboarding index

This folder gives a new AI session enough durable context to work on Slides DOCX without reconstructing the project from chat history. Start here, then read the files below in order. The repository code and tests remain the source of truth if these notes become stale.

## Reading order

1. [Product and workflows](01-product-and-workflows.md) — purpose, audience, user journeys, and product boundaries.
2. [Architecture](02-architecture.md) — entry points, processing flow, modules, GUI structure, and external tools.
3. [Behavioral contracts](03-behavioral-contracts.md) — defaults, precedence, file formats, profiles, and error behavior that changes must preserve.
4. [Development and testing](04-development-and-testing.md) — environment rules, test commands, CI, and useful verification.
5. [Decisions and roadmap](05-decisions-and-roadmap.md) — why the project has its current shape, known gaps, and deferred work.

Read the root [README](../README.md) when changing user-facing commands or documentation. It is the public manual; this folder is the maintainer handoff.

## Current snapshot

- Package: `presentation-transcript-docx` version `0.6.0`, Python `>=3.10`.
- Public CLI: `slides-docx`; source fallback: `python -m slides_docx`.
- Optional desktop GUI: `slides-docx-gui`; source fallback: `python -m slides_docx.gui`.
- Desktop stack: PySide6 with Qt Widgets, backed by the same synchronous service layer as the CLI.
- Packaged desktop targets: unsigned Linux x86_64 AppImage and ad-hoc-signed, unnotarized macOS 13+ Apple Silicon DMG. Both bundle Python, Qt, FFmpeg, and FFprobe.
- Windows packaging, Intel/universal macOS builds, and signed/notarized distribution remain deferred.
- Tests use `unittest`; CI covers CLI tests on Linux/macOS/Windows, the FFmpeg integration test on Linux, and Qt tests offscreen on Linux.

## Rules for future AI sessions

- Do not modify `.venv` or install packages into it. It may be used to run existing tools and tests. Use a disposable virtual environment under `/tmp` when different dependencies are needed.
- Keep business orchestration in `slides_docx/services.py`. The CLI and GUI should adapt service requests, results, progress, and errors rather than duplicate processing logic.
- Preserve `slides-docx` behavior unless the task explicitly changes the public interface.
- Do not restore the deleted legacy wrappers `detect_slides.sh`, `select_slides.py`, or `vttslidesdocx.py`. They were removed before the first published compatibility baseline.
- Treat files under `samples/` as private real-course material unless publication permission is established. Public screenshots and demos under `docs/assets/` use synthetic material.
- Keep FFmpeg and FFprobe external for normal CLI installations and bundled for self-contained desktop artifacts.
- Update this folder when architecture, public behavior, packaging targets, or major decisions change.
