import os
import tempfile
from pathlib import Path

from .content import screenshot_time
from .errors import SlidesDocxError
from .video import extract_frame


CONTACT_SHEET_COLUMNS = 4
THUMBNAIL_WIDTH = 240
THUMBNAIL_HEIGHT = 135
CELL_PADDING = 10
LABEL_HEIGHT = 30
CELL_WIDTH = THUMBNAIL_WIDTH + CELL_PADDING * 2
CELL_HEIGHT = THUMBNAIL_HEIGHT + LABEL_HEIGHT + CELL_PADDING * 2


def create_contact_sheet(
    video,
    slide_times,
    duration,
    output,
    crop=None,
    lead=5.0,
    progress=None,
    cancel=None,
):
    """Create a labeled four-column JPEG preview of all detected slides."""
    try:
        import cv2
        import numpy
    except ImportError as exc:
        raise SlidesDocxError(
            "OpenCV is required to create the contact sheet. Reinstall the package, "
            "or use --no-contact-sheet."
        ) from exc

    output = Path(output)
    if not output.parent.is_dir():
        raise SlidesDocxError(f"Contact-sheet directory does not exist: {output.parent}")

    starts = [0.0] + sorted(set(slide_times))
    rows = (len(starts) + CONTACT_SHEET_COLUMNS - 1) // CONTACT_SHEET_COLUMNS
    sheet = numpy.full(
        (rows * CELL_HEIGHT, CONTACT_SHEET_COLUMNS * CELL_WIDTH, 3),
        255,
        dtype=numpy.uint8,
    )

    with tempfile.TemporaryDirectory(prefix="slides_docx_contact_") as directory:
        directory = Path(directory)
        for index, start in enumerate(starts):
            if cancel is not None:
                cancel.raise_if_cancelled()
            end = starts[index + 1] if index + 1 < len(starts) else duration
            capture = screenshot_time(start, end, lead)
            frame_path = directory / f"slide_{index + 1:03d}.jpg"
            extract_frame(video, capture, frame_path, crop=crop, cancel=cancel)
            encoded = numpy.frombuffer(frame_path.read_bytes(), dtype=numpy.uint8)
            frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
            if frame is None:
                raise SlidesDocxError(
                    f"Could not read contact-sheet frame for slide {index + 1}."
                )

            scale = min(
                THUMBNAIL_WIDTH / frame.shape[1],
                THUMBNAIL_HEIGHT / frame.shape[0],
            )
            width = max(1, round(frame.shape[1] * scale))
            height = max(1, round(frame.shape[0] * scale))
            thumbnail = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

            row, column = divmod(index, CONTACT_SHEET_COLUMNS)
            cell_x = column * CELL_WIDTH
            cell_y = row * CELL_HEIGHT
            cv2.putText(
                sheet,
                f"Slide {index + 1:02d}",
                (cell_x + CELL_PADDING, cell_y + 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (30, 30, 30),
                1,
                cv2.LINE_AA,
            )
            image_x = cell_x + CELL_PADDING + (THUMBNAIL_WIDTH - width) // 2
            image_y = cell_y + LABEL_HEIGHT + CELL_PADDING + (THUMBNAIL_HEIGHT - height) // 2
            sheet[image_y:image_y + height, image_x:image_x + width] = thumbnail
            cv2.rectangle(
                sheet,
                (image_x, image_y),
                (image_x + width - 1, image_y + height - 1),
                (100, 100, 100),
                1,
            )
            if progress:
                progress(index + 1, len(starts))

    success, encoded_sheet = cv2.imencode(
        ".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88]
    )
    if not success:
        raise SlidesDocxError("OpenCV could not encode the contact sheet.")

    descriptor = None
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{output.stem}.", suffix=output.suffix, dir=output.parent
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded_sheet.tobytes())
        os.replace(temporary, output)
        temporary = None
    except OSError as exc:
        raise SlidesDocxError(f"Could not write contact sheet {output}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return output
