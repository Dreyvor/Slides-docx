import sys
import tempfile
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..errors import JobCancelledError, SlidesDocxError
from ..profiles import ProfileStore, format_crop, profile_settings, scale_profile
from ..services import (
    DEFAULT_CONTACT_SHEET,
    DEFAULT_LEAD,
    DEFAULT_MIN_GAP,
    DEFAULT_THRESHOLD,
    BuildRequest,
    CancellationToken,
    DetectRequest,
    PreviewRequest,
    build_docx,
    detect_slides,
    extract_preview,
    save_crop_profile,
)
from ..video import require_tool
from .theme import apply_system_theme
from .widgets import Collapsible, CropView


VIDEO_FILTER = "Videos (*.mp4 *.mkv *.mov *.avi *.webm *.m4v);;All files (*)"


class JobThread(QThread):
    progress = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.token = CancellationToken()

    def run(self):
        try:
            result = self.function(self.progress.emit, self.token)
        except JobCancelledError:
            self.cancelled.emit()
        except Exception as exc:  # GUI boundary: convert all failures to a dialog.
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)

    def cancel(self):
        self.token.cancel()


class PathRow(QWidget):
    changed = Signal(str)

    def __init__(self, mode="file", file_filter="All files (*)", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.file_filter = file_filter
        self.edit = QLineEdit()
        self.button = QPushButton("Browse…")
        self.button.clicked.connect(self.browse)
        self.edit.textChanged.connect(self.changed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def text(self):
        return self.edit.text().strip()

    def setText(self, value):
        self.edit.setText(str(value))

    def browse(self):
        start = self.text() or str(Path.home())
        if self.mode == "directory":
            selected = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        elif self.mode == "save":
            selected, _filter = QFileDialog.getSaveFileName(
                self, "Choose output", start, self.file_filter
            )
        else:
            selected, _filter = QFileDialog.getOpenFileName(
                self, "Choose file", start, self.file_filter
            )
        if selected:
            self.setText(selected)


class MainWindow(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store = store or ProfileStore()
        self.preview_result = None
        self.detect_result = None
        self.build_result = None
        self.worker = None
        self._temporary = tempfile.TemporaryDirectory(prefix="slides_docx_gui_")
        self.setWindowTitle("Slides DOCX")
        self.setMinimumSize(900, 680)
        self.setAcceptDrops(True)
        self._build_ui()
        self._refresh_profiles()

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        title = QLabel("Slides DOCX")
        title.setObjectName("title")
        subtitle = QLabel("Lecture recording + captions → illustrated, editable Word notes")
        subtitle.setObjectName("subtitle")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        self.step_label = QLabel()
        self.step_label.setObjectName("step")
        root_layout.addWidget(self.step_label)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._files_page())
        self.pages.addWidget(self._crop_page())
        self.pages.addWidget(self._detect_page())
        self.pages.addWidget(self._build_page())
        self.pages.currentChanged.connect(self._page_changed)
        root_layout.addWidget(self.pages, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_job)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.cancel_button)
        root_layout.addLayout(progress_row)

        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(150)
        root_layout.addWidget(Collapsible("Diagnostics", self.diagnostics))

        nav = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.next_button = QPushButton("Next")
        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        nav.addWidget(self.back_button)
        nav.addStretch()
        nav.addWidget(self.next_button)
        root_layout.addLayout(nav)
        self.setCentralWidget(root)
        self._page_changed(0)

    def _card(self, layout):
        card = QFrame()
        card.setObjectName("card")
        card.setLayout(layout)
        return card

    def _files_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel(
            "Choose the recording and its WebVTT captions. You can also drag both files "
            "onto this window."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.video_path = PathRow(file_filter=VIDEO_FILTER)
        self.vtt_path = PathRow(file_filter="WebVTT captions (*.vtt);;All files (*)")
        self.output_folder = PathRow(mode="directory")
        self.video_path.changed.connect(self._video_changed)
        form.addRow("Recording", self.video_path)
        form.addRow("Captions", self.vtt_path)
        form.addRow("Output folder", self.output_folder)
        layout.addWidget(self._card(form))
        layout.addStretch()
        return page

    def _crop_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(True)
        self.preview_time = QDoubleSpinBox()
        self.preview_time.setRange(0, 24 * 60 * 60)
        self.preview_time.setValue(30)
        self.preview_time.setSuffix(" s")
        self.load_preview_button = QPushButton("Load preview")
        self.load_preview_button.clicked.connect(self._load_preview)
        top.addWidget(QLabel("Profile"))
        top.addWidget(self.profile_combo, 1)
        top.addWidget(QLabel("Preview at"))
        top.addWidget(self.preview_time)
        top.addWidget(self.load_preview_button)
        layout.addLayout(top)
        self.crop_view = CropView()
        layout.addWidget(self.crop_view, 1)
        controls = QHBoxLayout()
        self.full_frame = QCheckBox("Use the full video frame")
        self.full_frame.toggled.connect(
            lambda checked: self.crop_view.setEnabled(not checked)
        )
        self.save_profile_button = QPushButton("Save selected area")
        self.save_profile_button.clicked.connect(self._save_profile)
        self.delete_profile_button = QPushButton("Delete profile")
        self.delete_profile_button.clicked.connect(self._delete_profile)
        controls.addWidget(self.full_frame)
        controls.addStretch()
        controls.addWidget(self.delete_profile_button)
        controls.addWidget(self.save_profile_button)
        layout.addLayout(controls)
        return page

    def _detect_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.detect_summary = QLabel("Run detection to create slide boundaries and a contact sheet.")
        self.detect_summary.setWordWrap(True)
        layout.addWidget(self.detect_summary)
        advanced = QWidget()
        form = QFormLayout(advanced)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0, 100)
        self.threshold.setValue(DEFAULT_THRESHOLD)
        self.threshold.setDecimals(1)
        self.min_gap = QDoubleSpinBox()
        self.min_gap.setRange(0, 60)
        self.min_gap.setValue(DEFAULT_MIN_GAP)
        self.min_gap.setSuffix(" s")
        self.contact_enabled = QCheckBox("Create contact sheet")
        self.contact_enabled.setChecked(DEFAULT_CONTACT_SHEET)
        self.manual_crop = QLineEdit()
        self.manual_crop.setPlaceholderText("Optional W:H:X:Y override")
        self.times_output = PathRow(mode="save", file_filter="Text files (*.txt)")
        self.contact_output = PathRow(mode="save", file_filter="JPEG image (*.jpg)")
        self.save_detect_settings = QCheckBox("Save these settings in the selected profile")
        form.addRow("Threshold", self.threshold)
        form.addRow("Minimum gap", self.min_gap)
        form.addRow("", self.contact_enabled)
        form.addRow("Manual crop", self.manual_crop)
        form.addRow("Slide-times file", self.times_output)
        form.addRow("Contact sheet", self.contact_output)
        form.addRow("", self.save_detect_settings)
        layout.addWidget(Collapsible("Advanced detection settings", advanced))
        row = QHBoxLayout()
        self.detect_button = QPushButton("Detect slides")
        self.detect_button.clicked.connect(self._run_detection)
        self.open_times_button = QPushButton("Open slide-times file")
        self.open_times_button.setEnabled(False)
        self.open_times_button.clicked.connect(
            lambda: self._open_path(self.detect_result.slide_times if self.detect_result else None)
        )
        self.open_contact_button = QPushButton("Open contact sheet")
        self.open_contact_button.setEnabled(False)
        self.open_contact_button.clicked.connect(
            lambda: self._open_path(self.detect_result.contact_sheet if self.detect_result else None)
        )
        row.addWidget(self.detect_button)
        row.addStretch()
        row.addWidget(self.open_times_button)
        row.addWidget(self.open_contact_button)
        layout.addLayout(row)
        self.contact_preview = QLabel("The contact sheet will appear here")
        self.contact_preview.setObjectName("contactPreview")
        self.contact_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.contact_preview.setMinimumHeight(260)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.contact_preview)
        layout.addWidget(scroll, 1)
        return page

    def _build_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.build_summary = QLabel("Create the editable Word document after reviewing detection.")
        self.build_summary.setWordWrap(True)
        layout.addWidget(self.build_summary)
        self.docx_output = PathRow(mode="save", file_filter="Word document (*.docx)")
        layout.addWidget(self._form_card("DOCX output", self.docx_output))
        advanced = QWidget()
        form = QFormLayout(advanced)
        self.use_date = QCheckBox("Prefix output with course date")
        self.course_date = QDateEdit(QDate.currentDate())
        self.course_date.setCalendarPopup(True)
        self.course_date.setDisplayFormat("dd.MM.yyyy")
        self.course_date.setEnabled(False)
        self.use_date.toggled.connect(self.course_date.setEnabled)
        self.lead = QDoubleSpinBox()
        self.lead.setRange(0, 3600)
        self.lead.setValue(DEFAULT_LEAD)
        self.lead.setSuffix(" s")
        self.save_build_settings = QCheckBox("Save lead time in the selected profile")
        form.addRow("", self.use_date)
        form.addRow("Course date", self.course_date)
        form.addRow("Screenshot lead", self.lead)
        form.addRow("", self.save_build_settings)
        layout.addWidget(Collapsible("Advanced document settings", advanced))
        actions = QHBoxLayout()
        self.build_button = QPushButton("Build DOCX")
        self.build_button.clicked.connect(self._run_build)
        self.open_docx_button = QPushButton("Open document")
        self.open_docx_button.setEnabled(False)
        self.open_docx_button.clicked.connect(
            lambda: self._open_path(self.build_result.output if self.build_result else None)
        )
        self.open_folder_button = QPushButton("Open output folder")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._open_output_folder)
        actions.addWidget(self.build_button)
        actions.addStretch()
        actions.addWidget(self.open_docx_button)
        actions.addWidget(self.open_folder_button)
        layout.addLayout(actions)
        self.done_label = QLabel()
        self.done_label.setObjectName("success")
        self.done_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.done_label.setWordWrap(True)
        layout.addWidget(self.done_label, 1)
        return page

    def _form_card(self, label, widget):
        form = QFormLayout()
        form.addRow(label, widget)
        return self._card(form)

    def _page_changed(self, index):
        names = ("Choose files", "Select slide area", "Detect and review", "Build document")
        self.step_label.setText(f"Step {index + 1} of 4 — {names[index]}")
        self.back_button.setEnabled(index > 0 and self.worker is None)
        self.next_button.setVisible(index < 3)
        self.next_button.setText("Review and build" if index == 2 else "Next")
        if index == 2:
            self.next_button.setEnabled(self.detect_result is not None and self.worker is None)
        else:
            self.next_button.setEnabled(self.worker is None)

    def _next(self):
        index = self.pages.currentIndex()
        if index == 0:
            if not self._validate_files():
                return
            self._refresh_profiles()
            self._set_default_outputs()
        elif index == 1:
            if not self.full_frame.isChecked() and not self._profile_name():
                self._show_error("Select or create a crop profile, or use the full frame.")
                return
            if not self.full_frame.isChecked():
                try:
                    _name, profile = self.store.get_profile(self._profile_name())
                except SlidesDocxError as exc:
                    self._show_error(str(exc))
                    return
                if profile is None:
                    self._show_error("Select or create a crop profile, or use the full frame.")
                    return
            self._load_profile_settings()
        elif index == 2 and self.detect_result is None:
            self._show_error("Run slide detection before building the document.")
            return
        self.pages.setCurrentIndex(index + 1)

    def _back(self):
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))

    def _validate_files(self):
        video = Path(self.video_path.text())
        vtt = Path(self.vtt_path.text())
        folder = Path(self.output_folder.text())
        if not video.is_file():
            self._show_error("Choose an existing video file.")
            return False
        if not vtt.is_file():
            self._show_error("Choose an existing WebVTT captions file.")
            return False
        if not folder.is_dir():
            self._show_error("Choose an existing output folder.")
            return False
        return True

    def _video_changed(self, value):
        video = Path(value)
        if video.is_file():
            if not self.vtt_path.text():
                candidate = video.with_suffix(".vtt")
                if candidate.is_file():
                    self.vtt_path.setText(candidate)
            if not self.output_folder.text():
                self.output_folder.setText(video.parent)

    def _set_default_outputs(self):
        video = Path(self.video_path.text())
        folder = Path(self.output_folder.text())
        if not self.times_output.text():
            self.times_output.setText(folder / f"{video.stem}.slide-times.txt")
        if not self.contact_output.text():
            self.contact_output.setText(folder / f"{video.stem}.contact-sheet.jpg")
        if not self.docx_output.text():
            self.docx_output.setText(folder / f"{video.stem}.docx")

    def _profile_name(self):
        name = self.profile_combo.currentText().strip()
        return name or None

    def _refresh_profiles(self, selected=None):
        try:
            data = self.store.load()
        except SlidesDocxError as exc:
            self._show_error(str(exc))
            return
        selected = selected or self.profile_combo.currentText().strip()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(sorted(data["profiles"]))
        active = selected or data.get("active_profile") or "default"
        self.profile_combo.setCurrentText(active)
        self.profile_combo.blockSignals(False)

    def _load_profile_settings(self):
        name = self._profile_name()
        if not name:
            return
        try:
            _name, profile = self.store.get_profile(name)
        except SlidesDocxError:
            return
        if profile is None:
            return
        detect = profile_settings(profile, "detect")
        build = profile_settings(profile, "build")
        self.threshold.setValue(detect.get("threshold", DEFAULT_THRESHOLD))
        self.min_gap.setValue(detect.get("min_gap", DEFAULT_MIN_GAP))
        self.contact_enabled.setChecked(
            detect.get("contact_sheet", DEFAULT_CONTACT_SHEET)
        )
        self.lead.setValue(build.get("lead", DEFAULT_LEAD))

    def _load_preview(self):
        if not Path(self.video_path.text()).is_file():
            self._show_error("Choose an existing video first.")
            return
        output = Path(self._temporary.name) / "preview.png"
        request = PreviewRequest(
            Path(self.video_path.text()), output, self.preview_time.value()
        )
        self._start_job(
            lambda progress, cancel: extract_preview(request, progress, cancel),
            self._preview_ready,
        )

    def _preview_ready(self, result):
        self.preview_result = result
        crop = None
        name = self._profile_name()
        if name:
            try:
                _name, profile = self.store.get_profile(name)
                if profile:
                    crop = scale_profile(profile, result.info.width, result.info.height)
            except SlidesDocxError as exc:
                self._append_log(str(exc))
        try:
            self.crop_view.set_image(result.image, crop)
        except ValueError as exc:
            self._show_error(str(exc))

    def _save_profile(self):
        if self.preview_result is None:
            self._show_error("Load a preview frame before selecting the slide area.")
            return
        crop = self.crop_view.selected_crop()
        if crop is None:
            self._show_error("Drag a rectangle around the slide area first.")
            return
        name = self._profile_name()
        if not name:
            self._show_error("Enter a profile name.")
            return
        try:
            save_crop_profile(name, crop, self.preview_result.info, self.store)
        except SlidesDocxError as exc:
            self._show_error(str(exc))
            return
        self._refresh_profiles(name)
        self._append_log(f"Saved profile '{name}': {format_crop(crop)}")

    def _delete_profile(self):
        name = self._profile_name()
        if not name:
            return
        try:
            self.store.delete(name)
        except SlidesDocxError as exc:
            self._show_error(str(exc))
            return
        self._refresh_profiles()
        self.crop_view.clear_selection()

    def _crop_options(self, for_build=False):
        manual = self.manual_crop.text().strip()
        if self.full_frame.isChecked():
            return {"crop": None, "profile": None, "no_crop": True}
        if manual:
            return {"crop": manual, "profile": None, "no_crop": False}
        # Build normally follows slide-times metadata so fingerprint checks remain active.
        return {
            "crop": None,
            "profile": None if for_build else self._profile_name(),
            "no_crop": False,
        }

    def _run_detection(self):
        if not self._validate_files():
            return
        crop = self._crop_options()
        settings_profile = None
        if not self.full_frame.isChecked() and not self.manual_crop.text().strip():
            settings_profile = self._profile_name()
        request = DetectRequest(
            video=Path(self.video_path.text()),
            output=Path(self.times_output.text()),
            contact_output=Path(self.contact_output.text()),
            threshold=self.threshold.value(),
            min_gap=self.min_gap.value(),
            contact_sheet=self.contact_enabled.isChecked(),
            persist_profile_settings=self.save_detect_settings.isChecked(),
            settings_profile=settings_profile,
            **crop,
        )
        self.detect_result = None
        self.next_button.setEnabled(False)
        self._start_job(
            lambda progress, cancel: detect_slides(
                request, self.store, progress, cancel
            ),
            self._detection_ready,
        )

    def _detection_ready(self, result):
        self.detect_result = result
        self.detect_summary.setText(
            f"Detected {result.slide_count} slides. Review the contact sheet before building."
        )
        self.open_times_button.setEnabled(True)
        self.open_contact_button.setEnabled(result.contact_sheet is not None)
        if result.contact_sheet and result.contact_sheet.is_file():
            from PySide6.QtGui import QPixmap

            pixmap = QPixmap(str(result.contact_sheet))
            self.contact_preview.setPixmap(
                pixmap.scaled(
                    820,
                    max(240, min(700, pixmap.height())),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.contact_preview.setText("Contact-sheet generation is disabled.")
        self.next_button.setEnabled(True)

    def _run_build(self):
        if self.detect_result is None:
            self._show_error("Run slide detection before building the document.")
            return
        selected_date = None
        if self.use_date.isChecked():
            value = self.course_date.date()
            selected_date = date(value.year(), value.month(), value.day())
        crop = self._crop_options(for_build=True)
        settings_profile = None
        if not self.full_frame.isChecked() and not self.manual_crop.text().strip():
            settings_profile = self._profile_name()
        request = BuildRequest(
            video=Path(self.video_path.text()),
            vtt=Path(self.vtt_path.text()),
            slide_times=self.detect_result.slide_times,
            output=Path(self.docx_output.text()),
            lead=self.lead.value(),
            course_date=selected_date,
            persist_profile_settings=self.save_build_settings.isChecked(),
            settings_profile=settings_profile,
            **crop,
        )
        self._start_job(
            lambda progress, cancel: build_docx(
                request, self.store, progress, cancel
            ),
            self._build_ready,
        )

    def _build_ready(self, result):
        self.build_result = result
        self.done_label.setText(
            f"Created {result.output.name}\n{result.slide_count} slides with editable transcript text"
        )
        self.open_docx_button.setEnabled(True)
        self.open_folder_button.setEnabled(True)

    def _start_job(self, function, success):
        if self.worker is not None:
            return
        worker = JobThread(function, self)
        self.worker = worker
        worker.progress.connect(self._job_progress)
        worker.succeeded.connect(success)
        worker.failed.connect(self._job_failed)
        worker.cancelled.connect(lambda: self._append_log("Cancelled."))
        worker.finished.connect(self._job_finished)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self.back_button.setEnabled(False)
        self.next_button.setEnabled(False)
        worker.start()

    def _job_progress(self, event):
        if event.fraction is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(event.fraction * 1000))
        if event.message:
            self._append_log(event.message)

    def _job_failed(self, message):
        self._append_log(f"Error: {message}")
        self._show_error(message)

    def _job_finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self._page_changed(self.pages.currentIndex())

    def _cancel_job(self):
        if self.worker:
            self.cancel_button.setEnabled(False)
            self.worker.cancel()

    def _append_log(self, message):
        if message:
            self.diagnostics.appendPlainText(message)

    def _show_error(self, message):
        QMessageBox.warning(self, "Slides DOCX", message)

    def _open_path(self, path):
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def _open_output_folder(self):
        if self.build_result:
            self._open_path(self.build_result.output.parent)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".vtt":
                self.vtt_path.setText(path)
            elif path.is_file():
                self.video_path.setText(path)
        event.acceptProposedAction()

    def closeEvent(self, event):
        if self.worker:
            self.worker.cancel()
            self.worker.wait(3000)
        self._temporary.cleanup()
        super().closeEvent(event)


def diagnose():
    ffmpeg = require_tool("ffmpeg")
    ffprobe = require_tool("ffprobe")
    import cv2  # noqa: F401
    import docx  # noqa: F401

    print(f"Slides DOCX {__version__}")
    print(f"ffmpeg: {ffmpeg}")
    print(f"ffprobe: {ffprobe}")
    print("GUI dependencies: OK")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(f"slides-docx-gui {__version__}")
        return 0
    if "--diagnose" in argv:
        return diagnose()
    application = QApplication([sys.argv[0], *argv])
    application.setApplicationName("Slides DOCX")
    application.setOrganizationName("slides-docx")
    apply_system_theme(application)
    icon = Path(__file__).with_name("icon.svg")
    if icon.is_file():
        application.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    application.styleHints().colorSchemeChanged.connect(
        lambda scheme: apply_system_theme(application, scheme)
    )
    window.show()
    return application.exec()
