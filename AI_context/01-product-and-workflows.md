# Product and workflows

## Product goal

Slides DOCX turns a recorded lecture plus timestamped WebVTT captions into an editable Word document organized by slide. It exists for students and teachers who want notes they can search, edit, annotate, and print without manually extracting every slide.

The product promise is:

> Lecture recording + captions → illustrated, editable Word notes

The tool detects visual slide transitions, captures the presentation area, assigns transcript cues to the corresponding slide, and normally creates alternating slide and transcript pages. Users who have the official slides can instead generate a transcript-only document with one portrait section per detected slide. The tool does not summarize or rewrite the transcript.

## Inputs and outputs

Normal inputs:

- One readable presentation video, usually MP4, MKV, MOV, AVI, WebM, or M4V.
- One UTF-8 WebVTT transcript.
- Optionally, a reusable crop profile describing the fixed slide rectangle.

Default outputs are placed beside the video:

- `<video-stem>.slide-times.txt`: editable transition timestamps plus crop metadata.
- `<video-stem>.contact-sheet.jpg`: a four-column visual review of detected slides.
- `<video-stem>.docx`: alternating slide/transcript pages by default, or portrait transcript-only sections when slide images are disabled.

With `--date DD.MM.YYYY`, the DOCX filename is prefixed as `YYYY_MM_DD-<filename>`.

## CLI workflow

For a new recording layout:

```bash
slides-docx select lecture.mp4 --profile university
slides-docx detect lecture.mp4
slides-docx build lecture.mp4 lecture.vtt
```

For later recordings using the active profile:

```bash
slides-docx detect lecture.mp4
slides-docx build lecture.mp4 lecture.vtt
```

The user should review the contact sheet after detection. They can correct the plain-text timestamp file before building when detection is imperfect.

## Desktop workflow

The GUI is a four-step guided window:

1. **Choose files** — video, VTT, and output folder; supports drag and drop and same-stem VTT discovery.
2. **Select slide area** — extract a preview, choose or create a profile, and drag a rectangle directly over the frame.
3. **Detect and review** — run cancellable detection, show progress, inspect the contact sheet, and open generated artifacts.
4. **Build document** — optionally add a course date, build the DOCX, and open the document or output folder.

Threshold, minimum transition gap, contact-sheet preference, slide-image preference, lead time, crop override, and output paths live in collapsed advanced panels. The GUI follows the operating system's light or dark appearance and responds to appearance changes while running.

## Product boundaries

Current behavior intentionally excludes:

- Transcript summarization, correction, translation, or semantic rewriting.
- OCR or comparison against the lecturer's original slide deck.
- Editing or deleting individual transitions inside the GUI. Users rerun detection or edit the timestamp file.
- A built-in updater, cloud service, account system, or telemetry.
- Automatic crop tracking when the slide area moves during a video.
- Mobile or web applications.

The slide area is assumed to remain fixed during a video. Crop profiles may be reused across recordings when the layout and aspect ratio are compatible.

## User-experience priorities

- Keep the recurring workflow short: detect, review, build.
- Make generated artifacts inspectable rather than hiding processing decisions.
- Use actionable errors for missing tools, unreadable inputs, invalid crops, changed profiles, and unwritable outputs.
- Keep advanced controls available without making them part of the first-run path.
- Preserve a headless CLI path through explicit `--crop` and `--no-crop` options.
