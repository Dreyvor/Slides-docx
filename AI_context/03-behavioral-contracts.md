# Behavioral contracts

## Commands and defaults

```text
slides-docx select VIDEO [--profile NAME] [--at TIMESTAMP]
slides-docx detect VIDEO [--output PATH] [--threshold N] [--min-gap SEC]
                         [--contact-output PATH]
                         [--contact-sheet | --no-contact-sheet]
                         [--crop W:H:X:Y | --profile NAME | --no-crop]
slides-docx build VIDEO VTT [--slide-times PATH] [--output PATH]
                       [--lead SEC] [--slide-images | --no-slide-images]
                       [--date DD.MM.YYYY]
                       [--crop W:H:X:Y | --profile NAME | --no-crop]
slides-docx profiles [list | activate NAME | delete NAME]
slides-docx completion {bash,zsh,fish,powershell}
```

Built-in defaults:

- Selection preview: 30 seconds, clamped to 0.15 seconds before video end.
- Profile name for selection: `default`.
- Scene threshold: `3.0`, valid range `0..100`.
- Minimum scene-change gap: `0.8` seconds; `0` disables merging.
- Contact sheet: enabled.
- Screenshot lead: `1.0` second.
- Slide screenshots in the DOCX: enabled.

The first slide always starts at `0.0`. A detection result containing `N` transitions represents `N + 1` slides.

## Crop resolution

`--crop`, `--profile`, and `--no-crop` are mutually exclusive in the CLI. Resolution behavior is:

1. `--no-crop` uses the full frame.
2. Explicit `--crop W:H:X:Y` uses that validated rectangle.
3. Explicit `--profile NAME` uses the named profile.
4. During build, slide-times metadata supplies the detection profile and expected fingerprint.
5. Otherwise the active profile is used.
6. If no profile exists, the full frame is used.

If detection used an explicit crop, build requires the user to pass that crop again because coordinates are deliberately not copied into the slide-times file. Explicit crop/profile/no-crop options during build override slide-times metadata.

Crop coordinates use FFmpeg order `W:H:X:Y`. Width and height must be positive; X and Y cannot be negative; the rectangle must fit the video.

Profiles reuse exact pixels when dimensions match. For a different resolution, normalized coordinates are scaled only when aspect-ratio drift is at most 1%. Scaled coordinates are clamped to the target frame. A larger change is rejected with instructions to select a new profile.

## Profile storage

`platformdirs.user_config_dir("slides-docx", roaming=True)` determines the configuration directory. Typical paths are:

- Linux: `$XDG_CONFIG_HOME/slides-docx/config.json`, otherwise `~/.config/slides-docx/config.json`.
- macOS: `~/Library/Application Support/slides-docx/config.json`.
- Windows: `%APPDATA%\slides-docx\config.json`.

Configuration version remains `1`. Each profile stores pixel coordinates, source dimensions, normalized coordinates, UTC update time, and optional grouped settings:

```json
{
  "version": 1,
  "active_profile": "university",
  "profiles": {
    "university": {
      "x": 100,
      "y": 40,
      "width": 1280,
      "height": 960,
      "source_width": 1920,
      "source_height": 1080,
      "normalized": {
        "x": 0.0520833333,
        "y": 0.0370370370,
        "width": 0.6666666667,
        "height": 0.8888888889
      },
      "updated_at": "<UTC ISO-8601>",
      "settings": {
        "detect": {
          "threshold": 6.0,
          "min_gap": 1.2,
          "contact_sheet": true
        },
        "build": {
          "lead": 2.0,
          "slide_images": false
        }
      }
    }
  }
}
```

Profile names accept ASCII letters, digits, dots, underscores, and hyphens and must begin with an alphanumeric character. Selecting an existing profile replaces its crop while preserving saved settings. The most recently selected profile becomes active. The active profile cannot be deleted until another profile is activated.

Writes are atomic through a temporary file followed by `os.replace`.

## Reusable settings

Effective value precedence is:

1. Value explicitly supplied for this operation.
2. Value stored in the resolved profile.
3. Built-in default.

The CLI saves supplied detection or build settings only when `--profile NAME` is explicitly present and only after the command succeeds. Omitted values do not overwrite saved values. Active or timestamp-referenced profiles contribute settings without being rewritten.

The GUI always presents concrete values, but it persists them only when the user checks the corresponding “Save … in the selected profile” checkbox. Crop fingerprints exclude settings, so changing threshold, gap, contact-sheet preference, lead, or slide-image preference does not invalidate previously detected timestamps.

## Generated filename extensions

Services enforce the only formats they generate. A missing or different final suffix is appended rather than replacing the user's filename:

- Slide times gain `.txt`.
- Contact sheets gain `.jpg`; existing `.jpg` or `.jpeg` suffixes are accepted case-insensitively.
- Documents gain `.docx`.

For example, `--output notes` becomes `notes.docx`, while `--output notes.final` becomes `notes.final.docx`. Date prefixes are applied after suffix normalization.

## Slide-times format and crop safety

Example:

```text
# slides-docx: 1
# crop-mode: profile
# crop-profile: university
# crop-fingerprint: 0123456789abcdef
32.4
71.933333
```

The fingerprint is the first 16 hexadecimal characters of a SHA-256 hash over crop pixels and source dimensions. It excludes normalized coordinates, timestamps, and saved settings.

During build, a changed fingerprint stops processing with an actionable error. This prevents detecting one region and silently capturing another. Numeric timestamps are deduplicated, sorted, restricted to `(0, video duration)`, and written with up to six decimal places. Unknown or malformed nonnumeric lines are ignored so users can annotate or manually edit the file.

## Detection and contact sheets

FFmpeg may report several detections during one animation or compression artifact. `merge_rapid_scene_changes` keeps the first timestamp from each cluster where consecutive detections are less than `min_gap` apart. A separation exactly equal to the gap remains a distinct transition.

The contact sheet uses the same crop as detection and one representative frame per slide. It has four columns, 240×135 thumbnail bounds, labels `Slide 01`, `Slide 02`, and so on, and JPEG quality 88. The default filename uses the video stem.

## Transcript and screenshot assignment

VTT parsing uses cue start times. It removes markup, decodes HTML entities, collapses whitespace, and skips malformed or empty cues. Each cue is assigned to the latest slide start at or before the cue time.

A representative screenshot is normally `lead` seconds before the next slide. For short intervals, selection moves to approximately 75% of the interval while remaining at least 0.15 seconds before the end when possible. The final slide uses the video duration as its end.

With slide images enabled, the DOCX contains, for each slide:

1. An A4 landscape page with a centered 25 cm-wide cropped PNG.
2. An A4 portrait page headed `Slide NN — HH:MM:SS.mmm` with editable transcript text.

Slides without assigned text contain `[No transcript assigned to this slide]`.

With slide images disabled, FFmpeg frame extraction is skipped. The document contains one A4 portrait transcript section per detected slide and no embedded image media. Screenshot lead and crop still resolve through normal settings and metadata paths, but they do not affect document content in this mode.

## Error and output behavior

- Public operational failures use `SlidesDocxError`; CLI errors are printed cleanly and return exit status `2`.
- Output directories must already exist.
- Timestamp output cannot overwrite the video.
- DOCX output cannot overwrite the video, VTT, or slide-times input.
- Failed or cancelled commands do not persist requested profile settings.
- Completion is best-effort and silent: unreadable configuration or filesystem entries reduce candidates without invoking FFmpeg, FFprobe, or OpenCV.
