# Development and testing

## Environment

Python 3.10 or newer is required. The normal development environment is created from the repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[gui]'
```

For this workspace, `.venv` already exists and must not be modified directly. Use it for existing commands when suitable. If a new dependency or isolated build is required, create a disposable environment under `/tmp`.

FFmpeg and FFprobe are required for integration tests and actual processing. The GUI can be tested headlessly with:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest tests.test_gui
```

## Useful commands

```bash
# Entire available suite
python3 -m unittest

# Core unit tests
python3 -m unittest tests.test_unit

# Real synthetic-video pipeline
python3 -m unittest tests.test_integration

# GUI tests with PySide6 installed
QT_QPA_PLATFORM=offscreen python3 -m unittest tests.test_gui

# Public entry points
python3 -m slides_docx --help
python3 -m slides_docx.gui --version

# Packaging and whitespace sanity
python3 -m build
git diff --check
bash -n packaging/build_appimage.sh packaging/linux/AppRun
```

Do not add low-value tests that merely repeat implementation. Add tests for behavioral contracts, regression-prone data flow, platform behavior, or destructive failure cases.

## Test organization

- `tests/test_unit.py` covers timestamp parsing, transcript assignment, screenshot timing, rapid transition merging, profile validation/scaling/settings/fingerprints, crop precedence, atomic metadata behavior, service requests/results, cancellation, tool lookup, output naming, and completion.
- `tests/test_integration.py` generates a synthetic Unicode-path video whose activity outside the crop changes independently. It verifies cropped detection, contact-sheet dimensions, saved profile settings, image and transcript-only DOCX layouts, automatic suffixes, PNG media, transcript assignment, and changed-profile rejection.
- `tests/test_gui.py` uses `QTest` with the offscreen platform to verify crop coordinate mapping, the four-page workflow, same-stem VTT discovery, full-frame navigation, document-image controls, dark/light selection, and palette fallback.

Tests use temporary directories and test-specific `ProfileStore` paths. Do not read or overwrite the developer's real profile configuration.

## CI

`.github/workflows/tests.yml` runs on every push and pull request:

- Unit tests and CLI smoke tests on Ubuntu, macOS, and Windows with Python 3.10.
- FFmpeg integration on Ubuntu.
- GUI tests on Ubuntu with `QT_QPA_PLATFORM=offscreen`.

`.github/workflows/release-linux.yml` runs manually and on `v*` tags. It builds on Ubuntu 22.04, runs tests, creates the AppImage, then smoke-tests its extracted contents on Ubuntu 22.04 and 24.04 under offscreen Qt, X11, and headless Wayland. Tagged runs publish GitHub Release assets after smoke tests pass.

The README badge points at the `tests.yml` workflow in `Dreyvor/Slides-docx`.

## Test fixtures and publication safety

The integration suite creates all media dynamically and is safe for CI. `docs/assets/` contains a synthetic four-slide demonstration created for public use.

`samples/` contains real physiology material and generated outputs. The directory is ignored by Git and must not be used in public screenshots, fixtures, releases, or bug reports without confirmed publication permission.

## Updating behavior

When changing a command or default:

1. Update the `argparse` definition and service request behavior.
2. Verify completion still derives the correct commands, choices, option values, and exclusions from the parser.
3. Update unit/integration tests for the public contract.
4. Update the root README and the relevant AI context page.
5. Run CLI help, source entry points, and packaging smoke checks.

When changing processing:

1. Keep CLI and GUI routed through the same service.
2. Preserve cancellation and progress reporting.
3. Verify paths with spaces and Unicode.
4. Verify the same resolved crop reaches detection, contact-sheet extraction, and DOCX screenshots.
5. Make output replacement atomic where practical.
