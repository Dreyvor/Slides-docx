# Decisions and roadmap

## Decisions already made

### One shared Python engine

The CLI processing was moved into synchronous services before adding the GUI. PySide6 is a thin desktop front end, not a second implementation. Go was considered, but Wails or Fyne would require rewriting the engine or shipping Python as a separate runtime.

### PySide6 and Qt Widgets

Qt Widgets was selected for a conventional desktop workflow, native dialogs, drag and drop, crop interaction, background threads, and cross-platform packaging. PySide6 remains optional so CLI installations stay lighter.

### Profiles are per OS user

Profiles are stored through `platformdirs`, outside the repository and pipx/frozen environments, so upgrades do not erase them and one recording layout can be reused across directories.

### Detection metadata protects build consistency

The slide-times file records a profile name and crop fingerprint instead of duplicating crop coordinates. Build refuses a changed profile. This favors explicit correction over silently producing mismatched screenshots.

### Contact sheets are the review mechanism

The first GUI does not provide a transition editor. The contact sheet reveals false or duplicate transitions; users rerun detection or edit the timestamp text file. This keeps the GUI small and the intermediate artifact transparent.

### Slide images are optional in the DOCX

Screenshots remain enabled by default for the original all-in-one notes workflow. Students who receive official lecture slides can disable them and use the contact sheet for rapid visual reference. Transcript-only documents preserve one portrait section per detected slide and avoid extracting or embedding frames.

### Settings persistence is explicit

Profiles may remember detection and build settings, but the CLI writes them only with explicit `--profile`, and the GUI requires an explicit save checkbox. Per-run overrides do not unexpectedly change shared configuration.

### Sole public interfaces

The project was unpublished when legacy wrappers were removed. `slides-docx`, `python -m slides_docx`, `slides-docx-gui`, and `python -m slides_docx.gui` are the supported interfaces. Backward compatibility starts with the first published release, not the deleted prototypes.

### Self-contained desktop releases

The first self-contained target was an unsigned Linux x86_64 AppImage. The second is a macOS 13+ Apple Silicon DMG. Both bundle Python, Qt, OpenCV, `python-docx`, FFmpeg, FFprobe, and license material so end users do not install runtimes. FFmpeg is pinned and built without GPL, nonfree, or version-3-only components.

The macOS preview is ad-hoc signed for Apple Silicon compatibility but has no Developer ID signature or notarization. Users follow Apple's per-application **Privacy & Security → Open Anyway** flow. Documentation must not recommend disabling Gatekeeper or globally removing quarantine. Managed Macs may prohibit the override.

### System-aware appearance

The GUI has explicit light and dark themes selected from the OS color scheme, with palette brightness as a fallback. Runtime appearance changes are applied without restarting. The crop canvas remains dark for image contrast.

### Presentation before additional features

Public demo assets use a synthetic lecture. The README leads with the transformation, visuals, first-run commands, and recurring workflow. A GUI was added as a guided wrapper, while the technical pipeline and advanced options remain documented later.

## Packaging state

Linux packaging consists of:

- `pyside6-deploy`/Nuitka standalone freezing.
- A standards-style AppDir with desktop entry and icon.
- A pinned LGPL-compatible FFmpeg 9.0.1 source build.
- `appimagetool` conversion, SHA-256 checksums, and dependency license inventory.
- Release smoke tests that extract the AppImage rather than requiring FUSE.

macOS packaging consists of:

- A macOS-specific `pyside6-deploy` template with Cocoa/Widgets/SVG/image plugin support.
- `Slides DOCX.app` metadata for bundle ID `io.github.dreyvor.slidesdocx`, Education category, minimum macOS 13, and package-sourced version.
- FFmpeg and FFprobe under `Contents/Resources/bin`, with notices and inventories under `Contents/Resources/licenses`.
- Arm64 thinning, nested ad-hoc signing, strict signature verification, and a compressed DMG with an Applications shortcut. Mach-O files and frameworks are signed from the inside out, followed by the outer bundle; `--deep` is used for verification rather than signing.
- A release smoke test that mounts the DMG, checks `--version`/`--diagnose`, starts Qt offscreen, and verifies there are no external non-system dynamic-library paths.

The macOS deployment excludes the GUI runtime SVG from Nuitka package data. The generated ICNS under `Contents/Resources` is the application icon; leaving the SVG under `Contents/MacOS` makes `codesign` interpret it as an unsigned nested code component.

Deployment specs are rendered with the active Python and checkout paths during each build; no machine-specific Python path is committed. The GUI project manifest explicitly includes `theme.py`.

A workflow file proves intended automation, not that a GitHub Release has actually completed. Check the repository's Actions and Releases state before claiming an artifact is published.

## Windows and additional macOS targets

No native Windows installer exists. The application code and bundled-tool resolver are designed for it, but the artifact must be built and tested on Windows.

Likely next packaging sequence:

1. Unsigned Windows x86_64 preview to validate demand and packaging.
2. Signed Windows distribution if nontechnical users adopt it.
3. Developer ID signing and notarization for the existing Apple Silicon DMG when a low-friction public release is justified.
4. Intel or universal macOS support only when user demand justifies the additional native-dependency work.

Unsigned Windows builds normally trigger SmartScreen and may be blocked by Smart App Control or institutional policy. Signing reduces friction but adds identity verification and recurring program/certificate costs.

## Deferred product work

- Windows packaging; Developer ID signing and notarization; Intel/universal macOS builds.
- In-GUI transition editing or deletion.
- Automatic updater or store distribution.
- Crash reporting or telemetry; none is currently collected.
- GUI automation beyond the present offscreen widget tests.
- Performance and memory validation with very large real lectures.
- Publishing to PyPI; installation currently assumes a cloned checkout or packaged desktop artifact.

## Known repository considerations

- There is no conventional `LICENSE` file. The README currently gives broad informal permission, but a standard license should be chosen before a formal public release if clear reuse terms matter.
- Generated package metadata, build products, samples, and the local venv are ignored. Avoid treating them as authoritative source files.
- `CODEX_CONTEXT.md` is ignored and predates this tracked context folder. Keep this folder authoritative and either reduce the old file to a local pointer or let it remain a personal convenience.
- Shell completion is intentionally dependency-free and must remain fast; it must not probe media or load GUI/tooling dependencies when the user presses Tab.

## Principles for future choices

- Prefer a small inspectable workflow over opaque automation.
- Preserve intermediate artifacts users can review and edit.
- Put reusable behavior in services before exposing it in a front end.
- Keep packaging choices compatible with bundled FFmpeg licensing and Qt/PySide licensing.
- Measure actual user friction before adding large features such as an updater, store release, or richer editor.
